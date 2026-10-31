from __future__ import annotations

from dataclasses import dataclass, replace

from app.document_transform.ir import BlockKind, DocumentIR
from app.document_transform.models import PatchGenerator
from app.document_transform.safety import PatchDecision, validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    TextPatch,
    TransformFinding,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import (
    MAX_MODEL_EDITABLE_BLOCK_CHARS,
    UnslopCandidate,
    deterministic_unslop_replacement,
    model_editable_blocks,
    scan_unslop_candidates,
)


@dataclass(frozen=True, slots=True)
class TransformOutcome:
    document: DocumentIR
    findings: tuple[TransformFinding, ...]
    applied_count: int
    deterministic_count: int
    model_count: int
    llm_invoked: bool


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
        allow_format_text_edits: bool = False,
        allow_unslop_model: bool = False,
    ) -> None:
        self._generator = generator
        self._allow_format_text_edits = allow_format_text_edits
        self._allow_unslop_model = allow_unslop_model

    async def transform(
        self,
        original: DocumentIR,
        request: DocumentTransformRequest,
    ) -> TransformOutcome:
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
                    outcome = _merge_outcomes(
                        outcome,
                        await self._model_phase(outcome.document, phase=TransformPhase.UNSLOP),
                    )
            else:
                outcome = replace(
                    outcome,
                    findings=outcome.findings + _contextual_review_findings(outcome.document),
                )
        if request.mode is not TransformMode.UNSLOP and self._allow_format_text_edits:
            outcome = _merge_outcomes(
                outcome, await self._model_phase(outcome.document, phase=TransformPhase.FORMAT)
            )
        return outcome

    async def _model_phase(
        self, document: DocumentIR, *, phase: TransformPhase
    ) -> TransformOutcome:
        patches = await self._generator.generate(document, phase=phase)
        aggressive = phase is TransformPhase.UNSLOP and self._allow_unslop_model
        decision = validate_patches(document, patches, phase=phase, aggressive_unslop=aggressive)
        if aggressive and decision.accepted:
            decision = await self._review_rewrites(document, decision)
        count = len(decision.accepted)
        return TransformOutcome(
            document=document.replace_blocks(decision.accepted),
            findings=decision.findings,
            applied_count=count,
            deterministic_count=0,
            model_count=count,
            llm_invoked=True,
        )

    async def _review_rewrites(
        self, document: DocumentIR, decision: PatchDecision
    ) -> PatchDecision:
        by_id = {block.id: block for block in document.blocks}
        proposed = [
            TextPatch(block_id=block_id, expected_sha256=by_id[block_id].sha256, replacement=text)
            for block_id, text in decision.accepted.items()
        ]
        verified = await self._generator.verify(document, proposed)
        rejected = tuple(
            TransformFinding(
                code="semantic_review_rejected",
                block_id=block_id,
                detail="The proposed rewrite did not pass independent fact and meaning preservation review",
            )
            for block_id in decision.accepted
            if block_id not in verified
        )
        return PatchDecision(
            accepted={
                block_id: text
                for block_id, text in decision.accepted.items()
                if block_id in verified
            },
            findings=decision.findings + rejected,
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
