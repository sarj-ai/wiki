from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Protocol
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
    DocumentGatewayError,
    InPlaceNeedsReviewError,
    InPlaceNotAllowedError,
    OutputConflictError,
    SourceChangedError,
    UnsupportedSourceError,
)
from app.document_transform.gateway import (
    DocumentGateway,
    InPlaceInspection,
    InPlaceStageResult,
    OutputDraft,
    SourceSnapshot,
    TabHeader,
)
from app.document_transform.ir import (
    DocumentIR,
    parse_google_document,
    parse_markdown,
    rewrite_markdown,
)
from app.document_transform.models import PatchGenerator
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    InPlaceCheckpoint,
    InPlaceCheckpointStage,
    OutputStrategy,
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
from app.identifiers import DocsRevisionId, DocsSegmentId, ModelId, TaskId


PIPELINE_VERSION = "document-transform-v3"
REQUEST_BUILDER_VERSION = "google-docs-requests-v2"
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


class InPlaceCheckpointStore(Protocol):
    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: InPlaceCheckpoint,
    ) -> int: ...


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
        checkpoint_store: InPlaceCheckpointStore | None = None,
        task_id: TaskId | None = None,
        lease_token: uuid.UUID | None = None,
        checkpoint: InPlaceCheckpoint | None = None,
        checkpoint_generation: int = 0,
    ) -> None:
        self._gateway = gateway
        self._generator = generator
        self._brand = brand or SarjBrandProfile()
        self._allow_format_text_edits = allow_format_text_edits
        self._logo_bytes = logo_bytes
        self._checkpoint_store = checkpoint_store
        self._task_id = task_id
        self._lease_token = lease_token
        self._checkpoint = checkpoint
        self._checkpoint_generation = checkpoint_generation

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
        if request.output_strategy is OutputStrategy.IN_PLACE:
            return await self._execute_in_place(operation_id=operation_id, request=request)
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

    async def _execute_in_place(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> DocumentTransformResult:
        if not isinstance(request.source, DriveFileSource):
            raise InPlaceNotAllowedError
        if self._checkpoint_store is None or self._task_id is None or self._lease_token is None:
            raise DocumentGatewayError

        snapshot = await self._gateway.load_drive_source(request.source)
        checkpoint = self._checkpoint
        if checkpoint is None and snapshot.modified_time != request.source.expected_modified_time:
            raise SourceChangedError
        if checkpoint is not None:
            if checkpoint.document_id != request.source.file_id:
                raise OutputConflictError
            checkpoint_contract = (
                checkpoint.source_modified_time,
                checkpoint.brand_profile_id,
                checkpoint.brand_profile_sha256,
                checkpoint.pipeline_version,
                checkpoint.request_builder_version,
                checkpoint.unslop_catalog_sha256,
            )
            current_contract = (
                request.source.expected_modified_time,
                CURRENT_BRAND_MANIFEST.profile_id,
                CURRENT_BRAND_MANIFEST.sha256,
                PIPELINE_VERSION,
                REQUEST_BUILDER_VERSION,
                UNSLOP_CATALOG_SHA256,
            )
            if checkpoint_contract != current_contract:
                raise OutputConflictError
            if checkpoint.stage is InPlaceCheckpointStage.COMPLETE:
                return self._in_place_result(
                    operation_id=operation_id,
                    request=request,
                    checkpoint=checkpoint,
                    output=DocumentOutput(
                        file_id=request.source.file_id,
                        name=snapshot.title,
                        parent_folder_id=snapshot.parent_folder_id,
                        web_url=snapshot.web_url,
                    ),
                )
        inspection = await self._gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
        if checkpoint is None:
            checkpoint = await self._plan_initial_in_place(
                operation_id=operation_id,
                request=request,
                snapshot=snapshot,
                inspection=inspection,
            )

        if checkpoint.stage is InPlaceCheckpointStage.HEADERS:
            stage_result = await self._apply_checkpoint_stage(checkpoint)
            stage_snapshot = SourceSnapshot(
                title=snapshot.title,
                parent_folder_id=snapshot.parent_folder_id,
                modified_time=snapshot.modified_time,
                revision_id=stage_result.revision_id,
                media_type=snapshot.media_type,
                google_document=stage_result.document,
                owned_by_me=snapshot.owned_by_me,
                can_edit=snapshot.can_edit,
                is_personal_drive=snapshot.is_personal_drive,
                web_url=snapshot.web_url,
            )
            inspection = await self._gateway.inspect_in_place(
                snapshot=stage_snapshot,
                title=snapshot.title,
            )
            header_expected = _to_ir(stage_snapshot).replace_blocks({})
            header_verification = await self._gateway.verify_in_place(
                document=stage_result.document,
                title=snapshot.title,
                expected=header_expected,
                structural_sha256=checkpoint.structural_sha256,
                protected_structure_sha256=checkpoint.protected_structure_sha256,
                verify_brand=False,
            )
            if not (
                header_verification.content_preserved and header_verification.structure_preserved
            ):
                raise InPlaceNeedsReviewError
            if (
                header_expected.semantic_sha256 != checkpoint.original_semantic_sha256
                or inspection.body_sha256 != checkpoint.body_sha256
                or inspection.header_safe_sha256 != checkpoint.header_safe_sha256
            ):
                raise InPlaceNeedsReviewError
            if inspection.missing_header_tab_ids or inspection.unknown_header_tab_ids:
                raise InPlaceNeedsReviewError
            snapshot = stage_snapshot
            original = _to_ir(stage_snapshot)
            transformed = original.replace_blocks(checkpoint.replacements)
            requests = self._content_requests(
                request=request,
                original=original,
                transformed=transformed,
                inspection=inspection,
            )
            checkpoint = checkpoint.model_copy(
                update={
                    "stage": InPlaceCheckpointStage.CONTENT,
                    "current_revision_id": stage_result.revision_id,
                    "marker_name": _in_place_marker(operation_id, "content", requests),
                    "marker_range": inspection.marker_range,
                    "mutation_sha256": mutation_sha256(requests),
                    "requests": requests,
                    "headers": {
                        str(header.tab_id): str(header.header_id) for header in inspection.headers
                    },
                }
            )
            checkpoint = await self._save_checkpoint(checkpoint)

        if checkpoint.stage is not InPlaceCheckpointStage.CONTENT:
            raise DocumentGatewayError
        if not checkpoint.requests:
            checkpoint = await self._save_checkpoint(
                checkpoint.model_copy(update={"stage": InPlaceCheckpointStage.COMPLETE})
            )
            return self._in_place_result(
                operation_id=operation_id,
                request=request,
                checkpoint=checkpoint,
                output=inspection.output,
            )
        before_content = _to_ir(snapshot)
        expected_content = before_content.replace_blocks(checkpoint.replacements)
        if expected_content.semantic_sha256 != checkpoint.expected_semantic_sha256:
            raise InPlaceNeedsReviewError
        stage_result = await self._apply_checkpoint_stage(checkpoint)
        verification = await self._gateway.verify_in_place(
            document=stage_result.document,
            title=snapshot.title,
            expected=expected_content,
            structural_sha256=checkpoint.structural_sha256,
            protected_structure_sha256=checkpoint.protected_structure_sha256,
            verify_brand=request.mode is not TransformMode.UNSLOP,
        )
        if not (
            verification.content_preserved
            and verification.structure_preserved
            and verification.brand_applied
        ):
            raise InPlaceNeedsReviewError
        checkpoint = await self._save_checkpoint(
            checkpoint.model_copy(
                update={
                    "stage": InPlaceCheckpointStage.COMPLETE,
                    "current_revision_id": stage_result.revision_id,
                    "requests": [],
                    "marker_name": None,
                    "marker_range": None,
                    "mutation_sha256": None,
                }
            )
        )
        return self._in_place_result(
            operation_id=operation_id,
            request=request,
            checkpoint=checkpoint,
            output=inspection.output,
        )

    async def _plan_initial_in_place(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        inspection: InPlaceInspection,
    ) -> InPlaceCheckpoint:
        source = request.source
        if not isinstance(source, DriveFileSource):
            raise InPlaceNotAllowedError
        if snapshot.revision_id is None:
            raise InPlaceNotAllowedError
        original = _to_ir(snapshot)
        outcome = await self._transform(original, request)
        _preflight_native_mutation_budget(
            request=request,
            snapshot=snapshot,
            original=original,
            transformed=outcome.document,
            brand=self._brand,
        )
        if outcome.findings:
            raise InPlaceNeedsReviewError
        branded = request.mode is not TransformMode.UNSLOP
        if branded and (inspection.unknown_header_tab_ids or not inspection.logo_uri):
            raise InPlaceNeedsReviewError
        replacements = {
            block.id: transformed.text
            for block, transformed in zip(original.blocks, outcome.document.blocks, strict=True)
            if block.text != transformed.text
        }
        model_ids = (
            [ModelId(value) for value in self._generator.model_ids] if outcome.llm_invoked else []
        )
        if branded and inspection.missing_header_tab_ids:
            requests = [
                {
                    "createHeader": {
                        "type": "DEFAULT",
                        "sectionBreakLocation": {"index": 0, "tabId": tab_id},
                    }
                }
                for tab_id in inspection.missing_header_tab_ids
            ]
            stage = InPlaceCheckpointStage.HEADERS
        else:
            requests = self._content_requests(
                request=request,
                original=original,
                transformed=outcome.document,
                inspection=inspection,
            )
            stage = InPlaceCheckpointStage.CONTENT
        changed = bool(requests)
        if requests and inspection.marker_range is None:
            raise InPlaceNeedsReviewError
        checkpoint = InPlaceCheckpoint(
            document_id=source.file_id,
            source_sha256=_source_sha256(snapshot, original),
            source_modified_time=source.expected_modified_time,
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            pipeline_version=PIPELINE_VERSION,
            request_builder_version=REQUEST_BUILDER_VERSION,
            unslop_catalog_sha256=UNSLOP_CATALOG_SHA256,
            original_semantic_sha256=original.semantic_sha256,
            expected_semantic_sha256=outcome.document.semantic_sha256,
            structural_sha256=inspection.structural_sha256,
            protected_structure_sha256=inspection.protected_structure_sha256,
            body_sha256=inspection.body_sha256,
            header_safe_sha256=inspection.header_safe_sha256,
            source_revision_id=snapshot.revision_id,
            current_revision_id=snapshot.revision_id,
            stage=stage,
            marker_name=_in_place_marker(operation_id, stage.value, requests) if requests else None,
            marker_range=inspection.marker_range if requests else None,
            mutation_sha256=mutation_sha256(requests) if requests else None,
            requests=requests,
            replacements=replacements,
            findings=[],
            applied_patch_count=outcome.applied_count,
            deterministic_patch_count=outcome.deterministic_count,
            model_patch_count=outcome.model_count,
            llm_invoked=outcome.llm_invoked,
            model_ids=model_ids,
            headers={str(header.tab_id): str(header.header_id) for header in inspection.headers},
            changed=changed,
        )
        return await self._save_checkpoint(checkpoint)

    def _content_requests(
        self,
        *,
        request: DocumentTransformRequest,
        original: DocumentIR,
        transformed: DocumentIR,
        inspection: InPlaceInspection,
    ) -> list[dict[str, Any]]:
        if request.mode is TransformMode.UNSLOP:
            return build_copy_text_mutations(original, transformed)
        return build_copy_mutations(
            original,
            transformed,
            profile=self._brand,
            headers=inspection.headers,
            logo_uri=inspection.logo_uri,
        )

    async def _apply_checkpoint_stage(self, checkpoint: InPlaceCheckpoint) -> InPlaceStageResult:
        if checkpoint.marker_name is None or checkpoint.marker_range is None:
            raise DocumentGatewayError
        if checkpoint.mutation_sha256 != mutation_sha256(checkpoint.requests):
            raise OutputConflictError
        return await self._gateway.apply_in_place_stage(
            document_id=checkpoint.document_id,
            required_revision_id=DocsRevisionId(checkpoint.current_revision_id),
            marker_name=checkpoint.marker_name,
            marker_range=checkpoint.marker_range,
            requests=checkpoint.requests,
        )

    async def _save_checkpoint(self, checkpoint: InPlaceCheckpoint) -> InPlaceCheckpoint:
        if self._checkpoint_store is None or self._task_id is None or self._lease_token is None:
            raise DocumentGatewayError
        self._checkpoint_generation = (
            await self._checkpoint_store.save_document_transform_checkpoint(
                self._task_id,
                lease_token=self._lease_token,
                expected_generation=self._checkpoint_generation,
                checkpoint=checkpoint,
            )
        )
        self._checkpoint = checkpoint
        return checkpoint

    @staticmethod
    def _in_place_result(
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        checkpoint: InPlaceCheckpoint,
        output: DocumentOutput,
    ) -> DocumentTransformResult:
        return DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=checkpoint.source_sha256,
            output=output,
            mode=request.mode,
            applied_patch_count=checkpoint.applied_patch_count,
            findings=checkpoint.findings,
            validation=ValidationSummary(
                source_unchanged=True,
                protected_content_preserved=True,
                structure_preserved=True,
                unsafe_patch_count=0,
            ),
            model_ids=checkpoint.model_ids,
            tool_versions={
                "unslop_catalog_sha256": UNSLOP_CATALOG_SHA256,
                "unslop_commit": UNSLOP_UPSTREAM_COMMIT,
                "unslop_version": UNSLOP_UPSTREAM_VERSION,
            },
            pipeline_version=PIPELINE_VERSION,
            needs_review=False,
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            deterministic_patch_count=checkpoint.deterministic_patch_count,
            model_patch_count=checkpoint.model_patch_count,
            llm_invoked=checkpoint.llm_invoked,
            output_strategy=OutputStrategy.IN_PLACE,
            changed=checkpoint.changed,
            source_revision_id=checkpoint.source_revision_id,
            output_revision_id=checkpoint.current_revision_id,
        )

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


def _in_place_marker(
    operation_id: uuid.UUID,
    stage: str,
    requests: list[dict[str, Any]],
) -> str:
    digest = mutation_sha256(requests)[:20]
    return f"sarj-transform-{operation_id.hex}-{stage}-{digest}"


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
