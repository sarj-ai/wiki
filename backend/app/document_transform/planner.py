from __future__ import annotations

from dataclasses import dataclass

from app.document_transform.ir import BlockKind, DocumentIR
from app.document_transform.models import PatchGenerator
from app.document_transform.safety import validate_patches
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
        current = original
        findings: list[TransformFinding] = []
        applied_count = 0
        deterministic_count = 0
        model_count = 0
        llm_invoked = False
        findings.extend(_unprocessed_findings(original, request=request))
        if request.mode is TransformMode.FORMAT:
            phases: tuple[TransformPhase, ...] = (TransformPhase.FORMAT,)
        elif request.mode is TransformMode.UNSLOP:
            phases = (TransformPhase.UNSLOP,)
        else:
            phases = (TransformPhase.UNSLOP, TransformPhase.FORMAT)
        for phase in phases:
            if phase is TransformPhase.UNSLOP:
                candidates = scan_unslop_candidates(current)
                if not candidates:
                    continue
                while candidates:
                    deterministic = _deterministic_unslop_patches(current, candidates)
                    if not deterministic:
                        break
                    decision = validate_patches(current, deterministic, phase=phase)
                    if not decision.accepted:
                        findings.extend(decision.findings)
                        break
                    current = current.replace_blocks(decision.accepted)
                    applied_count += len(decision.accepted)
                    deterministic_count += len(decision.accepted)
                    findings.extend(decision.findings)
                    candidates = scan_unslop_candidates(current)
                    if not candidates:
                        break
                if not candidates:
                    continue
                if not self._allow_unslop_model:
                    findings.extend(
                        TransformFinding(
                            code="contextual_unslop_review_required",
                            block_id=candidate.block_id,
                            detail=(
                                "A contextual writing pattern was left unchanged because this "
                                "artifact uses deterministic-only text edits"
                            ),
                        )
                        for candidate in candidates
                    )
                    continue
            if phase is TransformPhase.FORMAT and not self._allow_format_text_edits:
                continue
            llm_invoked = True
            patches = await self._generator.generate(current, phase=phase)
            decision = validate_patches(current, patches, phase=phase)
            current = current.replace_blocks(decision.accepted)
            applied_count += len(decision.accepted)
            model_count += len(decision.accepted)
            findings.extend(decision.findings)
        return TransformOutcome(
            current,
            tuple(findings),
            applied_count,
            deterministic_count,
            model_count,
            llm_invoked,
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
