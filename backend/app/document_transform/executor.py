from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any
import uuid

from app.document_transform.brand import (
    SarjBrandProfile,
    build_copy_mutations,
    build_copy_text_mutations,
    build_markdown_mutations,
    mutation_sha256,
)
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.errors import (
    OutputConflictError,
    SourceChangedError,
    UnsupportedSourceError,
)
from app.document_transform.gateway import DocumentGateway, OutputDraft, SourceSnapshot, TabHeader
from app.document_transform.ir import (
    DocumentIR,
    parse_google_document,
    parse_markdown,
    rewrite_markdown,
)
from app.document_transform.models import PatchGenerator
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    TextPatch,
    TransformFinding,
    TransformMode,
    TransformPhase,
    ValidationSummary,
)
from app.document_transform.unslop import (
    UNSLOP_CATALOG_SHA256,
    UNSLOP_UPSTREAM_COMMIT,
    UNSLOP_UPSTREAM_VERSION,
    UnslopCandidate,
    deterministic_unslop_replacement,
    scan_unslop_candidates,
)
from app.identifiers import DocsSegmentId, ModelId


PIPELINE_VERSION = "document-transform-v3"
_OUTPUT_SUFFIX = MappingProxyType(
    {
        TransformMode.FORMAT: "Sarj formatted",
        TransformMode.UNSLOP: "Unslopped",
        TransformMode.BOTH: "Sarj polished",
    }
)


@dataclass(frozen=True, slots=True)
class TransformOutcome:
    document: DocumentIR
    findings: tuple[TransformFinding, ...]
    applied_count: int
    deterministic_count: int
    model_count: int
    llm_invoked: bool


@dataclass(frozen=True, slots=True)
class AppliedValidation:
    protected_content_preserved: bool
    structure_preserved: bool
    findings: tuple[TransformFinding, ...]


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


class DocumentTransformExecutor:
    def __init__(
        self,
        *,
        gateway: DocumentGateway,
        generator: PatchGenerator,
        brand: SarjBrandProfile | None = None,
        allow_format_text_edits: bool = False,
        logo_bytes: bytes | None = None,
    ) -> None:
        self._gateway = gateway
        self._generator = generator
        self._brand = brand or SarjBrandProfile()
        self._allow_format_text_edits = allow_format_text_edits
        self._logo_bytes = logo_bytes

    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> DocumentTransformResult:
        if (
            self._brand.version != CURRENT_BRAND_MANIFEST.profile_id
            or self._brand.sha256 != CURRENT_BRAND_MANIFEST.sha256
        ):
            raise UnsupportedSourceError
        snapshot = await self._load_source(request)
        original = _to_ir(snapshot)
        source_sha256 = _source_sha256(snapshot, original)
        recovered = await self._gateway.recover_output(operation_id)
        if recovered is not None:
            if recovered.source_sha256 != source_sha256:
                raise OutputConflictError
            if recovered.completed:
                if recovered.result is not None:
                    return DocumentTransformResult.model_validate(recovered.result)
                return self._recovered_completed_result(
                    operation_id=operation_id,
                    request=request,
                    source_sha256=source_sha256,
                    draft=recovered,
                )

        outcome = await self._transform(original, request)
        findings = list(outcome.findings)
        if request.mode is TransformMode.UNSLOP:
            findings.extend(_markdown_fidelity_findings(snapshot))
        elif snapshot.markdown is not None:
            findings.append(
                TransformFinding(
                    code="structure_verification_required",
                    detail=(
                        "The native Google Doc was created, but Markdown import structure "
                        "requires review until output round-trip verification is available"
                    ),
                )
            )
            if self._logo_bytes is None:
                findings.append(
                    TransformFinding(
                        code="logo_unavailable",
                        detail="Typography and spacing were applied, but the Sarj logo was unavailable",
                    )
                )
        _preflight_native_mutation_budget(
            request=request,
            snapshot=snapshot,
            original=original,
            transformed=outcome.document,
            brand=self._brand,
        )
        await self._require_unchanged_drive_source(request, snapshot)
        draft = recovered or await self._create_output(
            operation_id=operation_id,
            request=request,
            snapshot=snapshot,
            source_sha256=source_sha256,
            original=original,
            transformed=outcome.document,
        )
        if request.mode is TransformMode.UNSLOP:
            prepared_draft = draft
            mutations = (
                build_copy_text_mutations(original, outcome.document)
                if snapshot.media_type == "application/vnd.google-apps.document"
                else build_markdown_mutations(
                    outcome.document,
                    tab_id=draft.tab_id,
                    profile=self._brand,
                    headers=(),
                    logo_uri="",
                    branded=False,
                )
            )
        elif snapshot.markdown is not None:
            prepared_draft = draft
            mutations = []
        else:
            prepared_draft = await self._gateway.prepare_brand_shell(
                operation_id=operation_id,
                draft=draft,
            )
            if prepared_draft.logo_uri is None:
                msg = "Brand shell did not provide a logo reference"
                raise RuntimeError(msg)
            findings.extend(
                TransformFinding(code=code, detail=_brand_warning_detail(code))
                for code in prepared_draft.brand_warnings
            )
            mutations = (
                build_copy_mutations(
                    original,
                    outcome.document,
                    profile=self._brand,
                    headers=prepared_draft.headers,
                    logo_uri=prepared_draft.logo_uri,
                )
                if isinstance(request.source, DriveFileSource)
                and snapshot.media_type == "application/vnd.google-apps.document"
                else build_markdown_mutations(
                    outcome.document,
                    tab_id=prepared_draft.tab_id,
                    profile=self._brand,
                    headers=prepared_draft.headers,
                    logo_uri=prepared_draft.logo_uri,
                )
            )
        applied = await self._gateway.apply_requests_once(
            operation_id=operation_id,
            draft=prepared_draft,
            mutation_sha256=mutation_sha256(mutations),
            requests=mutations,
            required_revision_id=prepared_draft.revision_id,
        )
        await self._require_unchanged_drive_source(request, snapshot)
        applied_validation = await self._validate_applied_output(
            request=request,
            snapshot=snapshot,
            expected=outcome.document,
            draft=applied,
            transform_findings=outcome.findings,
        )
        findings.extend(applied_validation.findings)
        validation = ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=applied_validation.protected_content_preserved,
            structure_preserved=applied_validation.structure_preserved,
            unsafe_patch_count=len(outcome.findings),
        )
        result = DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=source_sha256,
            output=applied.output,
            mode=request.mode,
            applied_patch_count=outcome.applied_count,
            findings=findings,
            validation=validation,
            model_ids=[ModelId(value) for value in self._generator.model_ids]
            if outcome.llm_invoked
            else [],
            tool_versions={
                "unslop_catalog_sha256": UNSLOP_CATALOG_SHA256,
                "unslop_commit": UNSLOP_UPSTREAM_COMMIT,
                "unslop_version": UNSLOP_UPSTREAM_VERSION,
            },
            pipeline_version=PIPELINE_VERSION,
            needs_review=bool(findings),
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            deterministic_patch_count=outcome.deterministic_count,
            model_patch_count=outcome.model_count,
            llm_invoked=outcome.llm_invoked,
        )
        await self._gateway.complete(
            operation_id=operation_id,
            draft=applied,
            result=result.model_dump(mode="json"),
        )
        return result

    async def _require_unchanged_drive_source(
        self,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
    ) -> None:
        if not isinstance(request.source, DriveFileSource):
            return
        unchanged = await self._gateway.source_is_unchanged(
            request.source,
            expected_modified_time=request.source.expected_modified_time,
            expected_revision_id=snapshot.revision_id,
        )
        if not unchanged:
            raise SourceChangedError

    @staticmethod
    def _recovered_completed_result(
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        source_sha256: str,
        draft: OutputDraft,
    ) -> DocumentTransformResult:
        finding = TransformFinding(
            code="recovered_completed_output",
            detail=(
                "The completed sibling was recovered after task interruption; "
                "review it because detailed patch telemetry was not persisted"
            ),
        )

        return DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=source_sha256,
            output=draft.output,
            mode=request.mode,
            applied_patch_count=0,
            findings=[finding],
            validation=ValidationSummary(
                source_unchanged=True,
                protected_content_preserved=False,
                structure_preserved=False,
                unsafe_patch_count=1,
            ),
            model_ids=[],
            tool_versions={
                "unslop_catalog_sha256": UNSLOP_CATALOG_SHA256,
                "unslop_commit": UNSLOP_UPSTREAM_COMMIT,
                "unslop_version": UNSLOP_UPSTREAM_VERSION,
            },
            pipeline_version=PIPELINE_VERSION,
            needs_review=True,
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        )

    async def _validate_applied_output(
        self,
        *,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        expected: DocumentIR,
        draft: OutputDraft,
        transform_findings: tuple[TransformFinding, ...],
    ) -> AppliedValidation:
        if not (
            isinstance(request.source, DriveFileSource)
            and snapshot.media_type == "application/vnd.google-apps.document"
        ):
            return AppliedValidation(
                protected_content_preserved=not transform_findings,
                structure_preserved=snapshot.markdown is None,
                findings=(),
            )
        verification = await self._gateway.verify_output(
            draft=draft,
            source=snapshot,
            expected=expected,
            verify_brand=request.mode is not TransformMode.UNSLOP,
        )
        findings: list[TransformFinding] = []
        if not verification.content_preserved:
            findings.append(
                TransformFinding(
                    code="post_write_content_mismatch",
                    detail="Output text did not match the validated transformation plan",
                )
            )
        if not verification.structure_preserved:
            findings.append(
                TransformFinding(
                    code="post_write_structure_mismatch",
                    detail="Output structure did not match the source document inventory",
                )
            )
        if not verification.brand_applied:
            findings.append(
                TransformFinding(
                    code="post_write_brand_mismatch",
                    detail="Output did not contain the complete pinned Sarj brand treatment",
                )
            )
        return AppliedValidation(
            protected_content_preserved=(not transform_findings and verification.content_preserved),
            structure_preserved=verification.structure_preserved,
            findings=tuple(findings),
        )

    async def _load_source(self, request: DocumentTransformRequest) -> SourceSnapshot:
        source = request.source
        if isinstance(source, DriveFileSource):
            snapshot = await self._gateway.load_drive_source(source)
            if snapshot.modified_time != source.expected_modified_time:
                raise SourceChangedError
            return snapshot
        return SourceSnapshot(
            title=source.name,
            parent_folder_id=source.destination_folder_id,
            modified_time=None,
            revision_id=None,
            media_type="text/markdown",
            markdown=source.content,
        )

    async def _transform(
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

    async def _create_output(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        source_sha256: str,
        original: DocumentIR,
        transformed: DocumentIR,
    ) -> OutputDraft:
        output_name = request.output_name or f"{snapshot.title} — {_OUTPUT_SUFFIX[request.mode]}"
        imported_docx = (
            render_markdown_docx(
                rewrite_markdown(snapshot.markdown, original, transformed),
                title=snapshot.title,
                logo_bytes=self._logo_bytes,
            )
            if snapshot.markdown is not None and request.mode is not TransformMode.UNSLOP
            else None
        )
        if isinstance(request.source, DriveFileSource):
            if snapshot.media_type != "application/vnd.google-apps.document":
                return await self._gateway.create_from_drive_markdown(
                    operation_id=operation_id,
                    source=request.source,
                    source_sha256=source_sha256,
                    output_name=output_name,
                    parent_folder_id=snapshot.parent_folder_id,
                    imported_docx=imported_docx,
                )
            return await self._gateway.create_from_drive_copy(
                operation_id=operation_id,
                source=request.source,
                source_sha256=source_sha256,
                output_name=output_name,
            )
        return await self._gateway.create_from_markdown(
            operation_id=operation_id,
            source=request.source,
            source_sha256=source_sha256,
            output_name=output_name,
            imported_docx=imported_docx,
        )


def _to_ir(snapshot: SourceSnapshot) -> DocumentIR:
    if snapshot.google_document is not None:
        if snapshot.google_document.get("revisionId") != snapshot.revision_id:
            raise SourceChangedError
        return parse_google_document(snapshot.google_document, title=snapshot.title)
    if snapshot.markdown is None:
        raise UnsupportedSourceError
    return parse_markdown(snapshot.markdown, title=snapshot.title)


def _source_sha256(snapshot: SourceSnapshot, document: DocumentIR) -> str:
    stable: dict[str, Any] = {
        "ir": document.text_sha256,
        "media_type": snapshot.media_type,
        "modified_time": snapshot.modified_time,
        "revision_id": snapshot.revision_id,
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _preflight_native_mutation_budget(
    *,
    request: DocumentTransformRequest,
    snapshot: SourceSnapshot,
    original: DocumentIR,
    transformed: DocumentIR,
    brand: SarjBrandProfile,
) -> None:
    if snapshot.media_type != "application/vnd.google-apps.document":
        return
    if request.mode is TransformMode.UNSLOP:
        requests = build_copy_text_mutations(original, transformed)
    else:
        headers = tuple(
            TabHeader(
                tab_id=tab_id,
                header_id=DocsSegmentId("preflight-header-segment"),
            )
            for tab_id in transformed.tab_ids
        )
        requests = build_copy_mutations(
            original,
            transformed,
            profile=brand,
            headers=headers,
            logo_uri="https://assets.sarj.ai/preflight-logo.png",
        )
    mutation_sha256(requests)


def _markdown_fidelity_findings(snapshot: SourceSnapshot) -> list[TransformFinding]:
    markdown = snapshot.markdown
    if markdown is None:
        return []
    findings: list[TransformFinding] = []
    if any(line.startswith("|") and line.endswith("|") for line in markdown.splitlines()):
        findings.append(
            TransformFinding(
                code="markdown_table_preserved_as_text",
                detail="Markdown tables remain pipe-delimited text",
            )
        )
    if any(token in markdown for token in ("**", "__", "](")):
        findings.append(
            TransformFinding(
                code="markdown_inline_markup_preserved_as_text",
                detail="Inline links or emphasis remain Markdown text for lossless review",
            )
        )
    return findings


def _brand_warning_detail(code: str) -> str:
    if code == "logo_unavailable":
        return "Typography and spacing were applied, but the logo URL was not publicly readable"
    return "Existing header preserved for review"
