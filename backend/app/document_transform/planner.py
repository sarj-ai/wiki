from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, replace

from loguru import logger

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import BlockKind, DocumentIR
from app.document_transform.models import (
    PatchGenerator,
    editorial_pass_scope,
    model_phase_review_scope,
)
from app.document_transform.safety import REPAIRABLE_REWRITE_CODES, PatchDecision, validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    FindingSeverity,
    TextPatch,
    TransformFinding,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import (
    CONCISION_WORD_DENOMINATOR,
    CONCISION_WORD_NUMERATOR,
    MAX_EDITORIAL_PASSES,
    MAX_MODEL_EDITABLE_BLOCK_CHARS,
    ConcisionTarget,
    UnslopCandidate,
    concision_target,
    deterministic_unslop_replacement,
    editorial_quality,
    emptied_section_headings,
    model_editable_blocks,
    prose_word_count,
    scan_unslop_candidates,
)
from app.performance import provider_retry_remaining_seconds


# Further passes are optional; retain time for a complete factual review and write.
_MIN_REFINEMENT_SECONDS = 180


@dataclass(frozen=True, slots=True)
class TransformOutcome:
    document: DocumentIR
    findings: tuple[TransformFinding, ...]
    applied_count: int
    deterministic_count: int
    model_count: int
    llm_invoked: bool


@dataclass(frozen=True, slots=True)
class _ReviewedRepair:
    decision: PatchDecision
    validated_ids: frozenset[str]


def _record_editorial_refinement(
    original: DocumentIR,
    outcome: TransformOutcome,
    *,
    attempted: int,
    accepted: int,
    stop_reason: str,
) -> None:
    logger.info(
        "document editorial refinement finished",
        source_sha256=original.text_sha256,
        result_sha256=outcome.document.text_sha256,
        editorial_attempted=attempted,
        editorial_accepted=accepted,
        editorial_stop_reason=stop_reason,
    )


def _best_reviewed_repairs(
    document: DocumentIR, approved: PatchDecision, repaired: PatchDecision
) -> PatchDecision:
    # Repairs are validated against the source; compare them with the
    # already reviewed draft as well before replacing any accepted passage.
    by_id = {block.id: block for block in document.blocks}
    return replace(
        repaired,
        accepted={
            block_id: text
            for block_id, text in repaired.accepted.items()
            if block_id not in approved.accepted
            or editorial_quality(by_id[block_id], text)
            < editorial_quality(by_id[block_id], approved.accepted[block_id])
        },
    )


def _retained_approved_ids(
    document: DocumentIR,
    approved: PatchDecision,
    repaired: PatchDecision,
    *,
    framing_source: DocumentIR | None = None,
) -> frozenset[str]:
    # A failed optional repair keeps an independently reviewed edit. Recheck its
    # literal constraints before discarding findings about the failed attempt.
    retained = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=approved.accepted[block.id],
            )
            for block in document.blocks
            if block.id in approved.accepted and block.id not in repaired.accepted
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
        framing_source=framing_source,
    )
    return frozenset(retained.accepted)


def _deterministic_unslop_patches(
    document: DocumentIR,
    candidates: tuple[UnslopCandidate, ...],
) -> list[TextPatch]:
    candidates_by_block: dict[str, list[UnslopCandidate]] = {}
    for candidate in candidates:
        candidates_by_block.setdefault(candidate.block_id, []).append(candidate)
    patches: list[TextPatch] = []
    for block in document.blocks:
        typed_candidates = tuple(
            candidate
            for candidate in candidates_by_block.get(block.id, [])
            if candidate.has_fixed_deletion
        )
        replacement = deterministic_unslop_replacement(block.text, typed_candidates)
        if replacement == block.text:
            continue
        patches.append(
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=replacement,
                rationale="deterministic fixed phrase deletion",
            )
        )
    return patches


class TextTransformPlanner:
    def __init__(
        self,
        *,
        generator: PatchGenerator,
        allow_unslop_model: bool = False,
        max_editorial_passes: int = MAX_EDITORIAL_PASSES,
    ) -> None:
        if not 1 <= max_editorial_passes <= MAX_EDITORIAL_PASSES:
            msg = "Editorial passes must be between 1 and 100"
            raise ValueError(msg)
        self._generator = generator
        self._allow_unslop_model = allow_unslop_model
        self._max_editorial_passes = max_editorial_passes

    async def transform(
        self,
        original: DocumentIR,
        request: DocumentTransformRequest,
    ) -> TransformOutcome:
        editable_ids = {block.id for block in model_editable_blocks(original)}
        concision_source = replace(
            original,
            blocks=tuple(
                block
                for block in original.blocks
                if block.id in editable_ids and concision_target(block) is not None
            ),
        )
        outcome = TransformOutcome(
            document=original,
            findings=_unprocessed_findings(original, request=request),
            applied_count=0,
            deterministic_count=0,
            model_count=0,
            llm_invoked=False,
        )
        if request.mode is not TransformMode.FORMAT:
            outcome = _merge_outcomes(outcome, _deterministic_unslop(original))
            if self._allow_unslop_model:
                if model_editable_blocks(outcome.document):
                    outcome = await self._refine_unslop(
                        outcome, original=original, concision_source=concision_source
                    )
                emptied = emptied_section_headings(original, outcome.document)
                outcome = replace(
                    outcome,
                    document=outcome.document.replace_blocks(dict.fromkeys(emptied, "")),
                    applied_count=outcome.applied_count + len(emptied),
                    deterministic_count=outcome.deterministic_count + len(emptied),
                )
            else:
                outcome = replace(
                    outcome,
                    findings=outcome.findings + _contextual_review_findings(outcome.document),
                )
            if self._allow_unslop_model and _document_concision_findings(
                concision_source, outcome.document
            ):
                outcome = replace(
                    outcome,
                    findings=outcome.findings
                    + tuple(
                        finding.model_copy(update={"severity": FindingSeverity.INFORMATIONAL})
                        for finding in _document_concision_findings(
                            concision_source, outcome.document
                        )
                    ),
                )
        return outcome

    async def _refine_unslop(
        self,
        outcome: TransformOutcome,
        *,
        original: DocumentIR,
        concision_source: DocumentIR,
    ) -> TransformOutcome:
        attempted = accepted = 0
        stop_reason = "pass_limit"
        for number in range(1, self._max_editorial_passes + 1):
            remaining = provider_retry_remaining_seconds()
            if number > 1 and remaining is not None and remaining < _MIN_REFINEMENT_SECONDS:
                stop_reason = "deadline_reserve"
                break
            attempted += 1
            try:
                with editorial_pass_scope(original, number=number):
                    following = await self._model_phase(
                        outcome.document,
                        phase=TransformPhase.UNSLOP,
                        concision_source=concision_source,
                        fidelity_source=original,
                    )
            except ModelUnavailableError as error:
                stop_reason = error.code
                if number == 1:
                    _record_editorial_refinement(
                        original,
                        outcome,
                        attempted=attempted,
                        accepted=accepted,
                        stop_reason=stop_reason,
                    )
                    raise
                break
            if following.document.text_sha256 == outcome.document.text_sha256:
                stop_reason = "no_verified_improvement" if following.findings else "converged"
                # A rejected optional attempt must not invalidate a reviewed draft.
                if number == 1:
                    outcome = _merge_outcomes(outcome, following)
                break
            outcome = _merge_refinement(outcome, following)
            accepted += 1
            if not model_editable_blocks(outcome.document):
                stop_reason = "not_eligible"
                break
        _record_editorial_refinement(
            original, outcome, attempted=attempted, accepted=accepted, stop_reason=stop_reason
        )
        return outcome

    @model_phase_review_scope()
    async def _model_phase(
        self,
        document: DocumentIR,
        *,
        phase: TransformPhase,
        concision_source: DocumentIR | None = None,
        fidelity_source: DocumentIR | None = None,
    ) -> TransformOutcome:
        patches = await self._generator.generate(document, phase=phase)
        aggressive = phase is TransformPhase.UNSLOP and self._allow_unslop_model
        decision = validate_patches(
            document,
            patches,
            phase=phase,
            aggressive_unslop=aggressive,
            framing_source=fidelity_source,
        )
        if aggressive and decision.accepted:
            decision = await self._review_rewrites(
                document, decision, fidelity_source=fidelity_source
            )
        feedback = decision.findings
        if aggressive and concision_source is not None:
            feedback += _document_concision_findings(
                concision_source, document.replace_blocks(decision.accepted)
            )
        if aggressive and feedback:
            # An optional repair outage must not discard an independently reviewed draft.
            with suppress(ModelUnavailableError):
                decision = await self._repair_rewrites(
                    document, patches, decision, feedback=feedback, fidelity_source=fidelity_source
                )
        count = len(decision.accepted)
        return TransformOutcome(
            document=document.replace_blocks(decision.accepted),
            findings=decision.findings,
            applied_count=count,
            deterministic_count=0,
            model_count=count,
            llm_invoked=True,
        )

    async def _repair_rewrites(
        self,
        document: DocumentIR,
        patches: list[TextPatch],
        decision: PatchDecision,
        *,
        feedback: tuple[TransformFinding, ...],
        fidelity_source: DocumentIR | None = None,
    ) -> PatchDecision:
        repair_ids = {
            finding.block_id for finding in feedback if finding.code in REPAIRABLE_REWRITE_CODES
        } - {
            finding.block_id for finding in feedback if finding.code not in REPAIRABLE_REWRITE_CODES
        }
        if not repair_ids:
            return decision
        restricted = replace(
            document, blocks=tuple(block for block in document.blocks if block.id in repair_ids)
        )
        repair = await self._attempt_repair(
            document, patches, feedback, restricted=restricted, fidelity_source=fidelity_source
        )
        repaired = _best_reviewed_repairs(document, decision, repair.decision)
        retained_ids = _retained_approved_ids(
            document, decision, repaired, framing_source=fidelity_source
        )
        remaining = tuple(
            finding
            for finding in decision.findings
            if finding.block_id not in repair.validated_ids
            or finding.code not in REPAIRABLE_REWRITE_CODES
        )
        previous = {(finding.code, finding.block_id) for finding in remaining}
        return PatchDecision(
            accepted={**decision.accepted, **repaired.accepted},
            findings=remaining
            + tuple(
                finding
                for finding in repaired.findings
                if finding.block_id not in retained_ids
                and (finding.code, finding.block_id) not in previous
            ),
        )

    async def _attempt_repair(
        self,
        document: DocumentIR,
        patches: list[TextPatch],
        findings: tuple[TransformFinding, ...],
        *,
        restricted: DocumentIR,
        fidelity_source: DocumentIR | None = None,
    ) -> _ReviewedRepair:
        proposals = await self._generator.repair(document, patches, findings)
        repaired = validate_patches(
            restricted,
            proposals,
            phase=TransformPhase.UNSLOP,
            aggressive_unslop=True,
            framing_source=fidelity_source,
        )
        validated_ids = frozenset(repaired.accepted)
        if repaired.accepted:
            repaired = await self._review_rewrites(
                restricted, repaired, fidelity_source=fidelity_source
            )
        return _ReviewedRepair(decision=repaired, validated_ids=validated_ids)

    async def _review_rewrites(
        self,
        document: DocumentIR,
        decision: PatchDecision,
        *,
        fidelity_source: DocumentIR | None = None,
    ) -> PatchDecision:
        baseline = fidelity_source if fidelity_source is not None else document
        by_id = {block.id: block for block in baseline.blocks}
        proposed = [
            TextPatch(block_id=block_id, expected_sha256=by_id[block_id].sha256, replacement=text)
            for block_id, text in decision.accepted.items()
        ]
        literal = validate_patches(
            baseline, proposed, phase=TransformPhase.UNSLOP, aggressive_unslop=True
        )
        proposed = [patch for patch in proposed if patch.block_id in literal.accepted]
        verified = await self._generator.review(baseline, proposed)
        rejected = tuple(
            TransformFinding(
                code="semantic_review_rejected",
                block_id=block_id,
                detail=verified.rejected.get(
                    block_id, "The rewrite changed an original constraint"
                ),
            )
            for block_id in decision.accepted
            if block_id not in verified.approved
        )
        return PatchDecision(
            accepted={
                block_id: text
                for block_id, text in decision.accepted.items()
                if block_id in verified.approved
            },
            findings=decision.findings + rejected,
        )


def _document_concision_findings(
    source: DocumentIR, candidate: DocumentIR
) -> tuple[TransformFinding, ...]:
    if not source.blocks:
        return ()
    source_words = sum(prose_word_count(block, block.text) for block in source.blocks)
    target = ConcisionTarget(
        source_words=source_words,
        max_words=source_words * CONCISION_WORD_NUMERATOR // CONCISION_WORD_DENOMINATOR,
    )
    by_id = {block.id: block for block in candidate.blocks}
    proposed_words = sum(
        prose_word_count(block, by_id.get(block.id, block).text) for block in source.blocks
    )
    if proposed_words <= target.max_words:
        return ()
    detail = (
        f"Document concision target unmet: source {target.source_words} words; "
        f"proposal {proposed_words} words; maximum {target.max_words} eligible prose words "
        "across the complete document. Unchanged and rejected blocks count at their retained "
        "length. Local block targets are advisory. Shorten further wherever faithful wording permits; never delete facts or "
        "qualifications to satisfy a word target."
    )
    return tuple(
        TransformFinding(code="concision_target_unmet", block_id=block.id, detail=detail)
        for block in source.blocks
    )


def _deterministic_unslop(document: DocumentIR) -> TransformOutcome:
    current = document
    count = 0
    findings: list[TransformFinding] = []
    while candidates := scan_unslop_candidates(current):
        patches = _deterministic_unslop_patches(current, candidates)
        if not patches:
            break
        decision = validate_patches(current, patches, phase=TransformPhase.UNSLOP)
        findings.extend(decision.findings)
        if not decision.accepted:
            break
        current = current.replace_blocks(decision.accepted)
        count += len(decision.accepted)
    return TransformOutcome(
        document=current,
        findings=tuple(findings),
        applied_count=count,
        deterministic_count=count,
        model_count=0,
        llm_invoked=False,
    )


def _contextual_review_findings(document: DocumentIR) -> tuple[TransformFinding, ...]:
    return tuple(
        TransformFinding(
            code="contextual_unslop_review_required",
            block_id=candidate.block_id,
            detail="A contextual writing pattern was left unchanged because this artifact uses deterministic-only text edits",
        )
        for candidate in scan_unslop_candidates(document)
    )


def _merge_outcomes(first: TransformOutcome, following: TransformOutcome) -> TransformOutcome:
    return TransformOutcome(
        document=following.document,
        findings=first.findings + following.findings,
        applied_count=first.applied_count + following.applied_count,
        deterministic_count=first.deterministic_count + following.deterministic_count,
        model_count=first.model_count + following.model_count,
        llm_invoked=first.llm_invoked or following.llm_invoked,
    )


def _merge_refinement(first: TransformOutcome, following: TransformOutcome) -> TransformOutcome:
    changed_ids = {
        after.id
        for before, after in zip(first.document.blocks, following.document.blocks, strict=True)
        if before.text != after.text
    }
    return _merge_outcomes(
        replace(
            first,
            findings=tuple(
                finding
                for finding in first.findings
                if finding.block_id not in changed_ids
                or finding.code not in REPAIRABLE_REWRITE_CODES
            ),
        ),
        following,
    )


def _unprocessed_findings(
    document: DocumentIR,
    *,
    request: DocumentTransformRequest,
) -> tuple[TransformFinding, ...]:
    findings: list[TransformFinding] = []
    if document.source_format == "google_doc":
        findings.extend(
            TransformFinding(
                code="google_doc_text_target_unsupported",
                block_id=block.id,
                detail=(
                    "Visible native Docs text was preserved but not transformed because its "
                    "rich structure is unsupported"
                ),
            )
            for block in document.blocks
            if block.text.strip() and not block.editable and block.kind is not BlockKind.BLANK
        )
    if request.mode in {TransformMode.UNSLOP, TransformMode.BOTH}:
        findings.extend(
            TransformFinding(
                code="unslop_block_too_large",
                block_id=block.id,
                detail="The text block exceeded the deterministic Unslop scan limit",
            )
            for block in document.blocks
            if block.editable and len(block.text) > MAX_MODEL_EDITABLE_BLOCK_CHARS
        )
    return tuple(findings)
