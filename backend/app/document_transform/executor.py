from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from types import MappingProxyType
from typing import Any, NamedTuple, Protocol, override
import uuid

from loguru import logger

from app.document_transform.authored_labels import (
    AuthoredLabelRole,
    apply_authored_labels,
    plan_authored_labels,
)
from app.document_transform.brand import (
    SarjBrandProfile,
    build_copy_mutations,
    build_copy_text_mutations,
    build_custom_header_title_requests,
    mutation_sha256,
    preserve_in_place_native_list_text_styles,
)
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.copy_font_defense import (
    copy_font_proofs,
    copy_preserved_font_ids,
    defend_copy_fonts,
)
from app.document_transform.custom_furniture import custom_furniture_requests_safe
from app.document_transform.docx_renderer import markdown_import_manifest, render_markdown_docx
from app.document_transform.errors import (
    BrandAssetUnavailableError,
    CustomHeaderNeedsReviewError,
    DestinationAccessChangedError,
    DocumentGatewayError,
    InPlaceNeedsReviewError,
    InPlaceNotAllowedError,
    InPlacePostMutationVerificationError,
    OutputConflictError,
    OutputVerificationError,
    SourceChangedError,
    SourceTooLargeError,
    UnsupportedSourceError,
    UnsupportedTabMetadataError,
)
from app.document_transform.gateway import (
    DocumentGateway,
    InPlaceInspection,
    InPlaceStageResult,
    OutputDraft,
    OutputVerification,
    SourceSnapshot,
    TabHeader,
)
from app.document_transform.header_provenance import HeaderIdentityFinalization
from app.document_transform.header_recovery import recover_empty_header_postcondition
from app.document_transform.ir import (
    MAX_DOC_TABS,
    DocumentIR,
    parse_google_document,
    parse_markdown,
    rewrite_markdown,
)
from app.document_transform.models import PatchGenerator
from app.document_transform.naming import (
    polished_document_name,
    suggested_document_title,
    title_case_document_name,
)
from app.document_transform.native_font_defense import (
    apply_native_font_preservation,
    defend_native_font_requests,
    preserved_native_font_blocks,
)
from app.document_transform.native_font_noop import filter_redundant_native_fonts
from app.document_transform.native_font_proof import (
    NativeFontBlockId,
    NativeFontVerificationPolicy,
    NativeVisualAuthoritySelection,
    capture_native_font_proof,
    verify_native_font_proof,
)
from app.document_transform.native_footer_flow import footer_coverage_complete
from app.document_transform.native_pagination import (
    NativePaginationRule,
    apply_native_pagination,
    existing_native_pagination,
    plan_native_pagination,
)
from app.document_transform.native_paragraph_layout import (
    NativeParagraphLayout,
    apply_paragraph_layout,
    capture_paragraph_layout,
    paragraph_layout_requests_safe,
    verify_paragraph_layout,
)
from app.document_transform.native_preservation import preserved_native_findings
from app.document_transform.native_table_font import (
    TableFontPlan,
    plan_table_fonts,
    project_table_fonts,
    table_font_proof_matches,
    table_font_requests,
)
from app.document_transform.native_visual_authority import (
    apply_visual_authority,
    visual_authority_block_ids,
)
from app.document_transform.planner import TextTransformPlanner, TransformOutcome
from app.document_transform.runner import DocumentTransformRunner
from app.document_transform.schemas import (
    CopyCheckpoint,
    CopyCheckpointStage,
    DocumentOutput,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    FindingSeverity,
    InPlaceCheckpoint,
    InPlaceCheckpointStage,
    OutputStrategy,
    QaCopyPermissionEvidence,
    QaDestinationPermissionBinding,
    QaDocumentTransformRequest,
    QaDocumentTransformResult,
    QaQuarantinedOutputEvidence,
    TabNamingDeclinedReason,
    TransformFinding,
    TransformMode,
    ValidationSummary,
)
from app.document_transform.tab_structure_execution import (
    StructureStage,
    planned_structure_document,
    prepare_content_move,
    prepare_tab_creation,
    recover_created_tabs,
    verify_structure_content,
)
from app.document_transform.unslop import (
    UNSLOP_CATALOG_SHA256,
    UNSLOP_UPSTREAM_COMMIT,
    UNSLOP_UPSTREAM_VERSION,
)
from app.document_transform.versions import DOCUMENT_PIPELINE_VERSION
from app.identifiers import DocsRevisionId, DocsSegmentId, FileId, ModelId, TaskId


class _TableFontScope(NamedTuple):
    plan: TableFontPlan | None
    protected_sha256: str
    requests: list[dict[str, Any]]
    findings: list[TransformFinding]


PIPELINE_VERSION = DOCUMENT_PIPELINE_VERSION
REQUEST_BUILDER_VERSION = "google-docs-requests-v26"
IN_PLACE_REQUEST_BUILDER_VERSION = f"{REQUEST_BUILDER_VERSION}-header-title-v1"
_COPY_REQUEST_BUILDER_VERSION = f"{REQUEST_BUILDER_VERSION}-visual-v1"
_MAX_OUTPUT_NAME_LENGTH = 255
_MAX_GENERATED_NAME_SUFFIXES = 16
_OUTPUT_SUFFIX = MappingProxyType(
    {
        TransformMode.FORMAT: "Sarj formatted",
        TransformMode.UNSLOP: "Unslopped",
        TransformMode.BOTH: "Sarj polished",
    }
)


class StructureInputs(NamedTuple):
    snapshot: SourceSnapshot
    expected: DocumentIR


def _structure_inputs(
    snapshot: SourceSnapshot, expected: DocumentIR, checkpoint: CopyCheckpoint
) -> StructureInputs:
    structure = checkpoint.tab_structure
    if structure is None:
        return StructureInputs(snapshot, expected)
    if snapshot.google_document is None or structure.stage is not StructureStage.VERIFIED:
        raise OutputVerificationError
    projected = planned_structure_document(structure, snapshot.google_document)
    mapped = parse_google_document(projected, title=expected.title)
    if len(mapped.blocks) != len(expected.blocks):
        raise OutputVerificationError
    mapped = replace(
        mapped,
        copy_font_defended=expected.copy_font_defended,
        blocks=tuple(
            replace(
                block,
                text=transformed.text,
                kind=transformed.kind,
                level=transformed.level,
                pagination_keep_with_next=transformed.pagination_keep_with_next,
                suppress_space_above=transformed.suppress_space_above,
                suppress_space_below=transformed.suppress_space_below,
                preserved_visual_authority=transformed.preserved_visual_authority,
                preserved_font_family=transformed.preserved_font_family,
            )
            for block, transformed in zip(mapped.blocks, expected.blocks, strict=True)
        ),
    )
    return StructureInputs(replace(snapshot, google_document=projected), mapped)


@dataclass(frozen=True, slots=True)
class AppliedValidation:
    protected_content_preserved: bool
    structure_preserved: bool
    supported_brand_applied: bool
    findings: tuple[TransformFinding, ...]
    revision_id: DocsRevisionId


class DocumentTransformCheckpointStore(Protocol):
    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int: ...

    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None: ...


class QaDestinationPermissionVerifier(Protocol):
    async def __call__(
        self,
        destination_folder_id: FileId,
        binding: QaDestinationPermissionBinding,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class QaCreatedCopyPermissionRequest:
    output_file_id: FileId
    destination_folder_id: FileId
    operation_id: uuid.UUID
    source_sha256: str
    output_revision_id: DocsRevisionId
    binding: QaDestinationPermissionBinding


@dataclass(frozen=True, slots=True)
class AttestedCopy:
    result: DocumentTransformResult
    checkpoint: CopyCheckpoint


class QaCreatedCopyVerifier(Protocol):
    async def __call__(
        self,
        request: QaCreatedCopyPermissionRequest,
    ) -> QaCopyPermissionEvidence: ...


class DocumentTransformExecutor(DocumentTransformRunner):
    def __init__(
        self,
        *,
        gateway: DocumentGateway,
        generator: PatchGenerator,
        brand: SarjBrandProfile | None = None,
        allow_format_text_edits: bool = False,
        logo_bytes: bytes | None = None,
        checkpoint_store: DocumentTransformCheckpointStore | None = None,
        task_id: TaskId | None = None,
        lease_token: uuid.UUID | None = None,
        checkpoint: DocumentTransformCheckpoint | None = None,
        checkpoint_generation: int = 0,
        qa_destination_permission_verifier: QaDestinationPermissionVerifier | None = None,
        qa_created_copy_verifier: QaCreatedCopyVerifier | None = None,
    ) -> None:
        self._gateway = gateway
        self._generator = generator
        self._planner = TextTransformPlanner(
            generator=generator,
            allow_format_text_edits=allow_format_text_edits,
        )
        self._brand = brand or SarjBrandProfile()
        self._allow_format_text_edits = allow_format_text_edits
        self._logo_bytes = logo_bytes
        self._checkpoint_store = checkpoint_store
        self._task_id = task_id
        self._lease_token = lease_token
        self._checkpoint = checkpoint
        self._checkpoint_generation = checkpoint_generation
        self._qa_destination_permission_verifier = qa_destination_permission_verifier
        self._qa_created_copy_verifier = qa_created_copy_verifier

    @override
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
        return await self._execute_copy(operation_id=operation_id, request=request)

    async def _execute_copy(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> DocumentTransformResult:
        snapshot = await self._load_source(request)
        original = _to_ir(snapshot)
        original = apply_visual_authority(
            original,
            visual_authority_block_ids(snapshot.google_document, original)
            if request.mode is not TransformMode.UNSLOP
            else (),
        )
        original = replace(
            original,
            copy_font_defended=request.mode is not TransformMode.UNSLOP
            and any(not block.editable and block.font_style_ranges for block in original.blocks),
        )
        source_sha256 = _source_sha256(snapshot, original)
        recovered = await self._gateway.recover_output(operation_id)
        checkpoint = self._copy_checkpoint_or_none()
        if recovered is not None and recovered.output.parent_folder_id != _copy_output_parent(
            request, snapshot
        ):
            # A QA recovery candidate is untrusted until its current effective ACL
            # and exact destination parent have been proved.  Do not let the
            # generic conflict shortcut bypass that proof.
            if _qa_destination_permission(request) is not None:
                try:
                    await self._require_qa_created_copy_permission(
                        operation_id=operation_id,
                        source_sha256=source_sha256,
                        request=request,
                        draft=recovered,
                    )
                except DestinationAccessChangedError:
                    if checkpoint is not None:
                        await self._record_qa_quarantine(
                            checkpoint=checkpoint,
                            operation_id=operation_id,
                            source_sha256=source_sha256,
                            request=request,
                            draft=recovered,
                        )
                    raise
            raise OutputConflictError
        if checkpoint is None:
            if recovered is not None:
                # Legacy/incomplete outputs without a durable plan cannot be
                # re-attested safely and must never trigger a fresh model plan.
                raise OutputConflictError
            outcome = await self._planner.transform(
                apply_visual_authority(original, _copy_visual_ids(original), planner=True), request
            )
            outcome = replace(
                outcome,
                document=replace(
                    outcome.document,
                    blocks=tuple(
                        before if before.preserved_visual_authority else after
                        for before, after in zip(
                            original.blocks, outcome.document.blocks, strict=True
                        )
                    ),
                ),
                findings=tuple(
                    finding
                    for finding in outcome.findings
                    if not (
                        finding.block_id in _copy_visual_ids(original)
                        and finding.code == "google_doc_text_target_unsupported"
                    )
                )
                + tuple(
                    TransformFinding(
                        code="native_visual_authority_preserved",
                        block_id=block_id,
                        detail="Authored title size, color, alignment, font family, and emphasis were preserved unchanged; wording cleanup was skipped for this paragraph. Full Sarj typography was not applied.",
                        severity=FindingSeverity.INFORMATIONAL,
                    )
                    for block_id in _copy_visual_ids(original)
                ),
            )
            if request.mode is not TransformMode.UNSLOP:
                outcome = replace(
                    outcome,
                    document=apply_authored_labels(
                        outcome.document, plan_authored_labels(original)
                    ),
                )
                outcome = replace(
                    outcome,
                    document=apply_native_pagination(
                        outcome.document,
                        plan_native_pagination(snapshot.google_document, outcome.document),
                    ),
                )
            outcome = _copy_font_outcome(snapshot, original, outcome)
            checkpoint = await self._save_copy_checkpoint(
                _new_copy_checkpoint(
                    request=request,
                    snapshot=snapshot,
                    original=original,
                    outcome=outcome,
                    source_sha256=source_sha256,
                    model_ids=(
                        [ModelId(value) for value in self._generator.model_ids]
                        if outcome.llm_invoked
                        else []
                    ),
                    logo_sha256=(
                        hashlib.sha256(self._logo_bytes).hexdigest()
                        if self._logo_bytes is not None
                        else None
                    ),
                )
            )
        else:
            _validate_copy_checkpoint(
                checkpoint,
                request=request,
                snapshot=snapshot,
                original=original,
                source_sha256=source_sha256,
                current_logo_sha256=(
                    hashlib.sha256(self._logo_bytes).hexdigest()
                    if self._logo_bytes is not None
                    else None
                ),
            )
            outcome = _copy_checkpoint_outcome(checkpoint, original)

        if recovered is not None and (
            recovered.source_sha256 != source_sha256
            or (
                checkpoint.output is not None
                and recovered.output.file_id != checkpoint.output.file_id
            )
        ):
            raise OutputConflictError
        if checkpoint.stage in {CopyCheckpointStage.VERIFIED, CopyCheckpointStage.ATTESTED}:
            if recovered is None or checkpoint.result is None:
                raise OutputConflictError
            snapshot, original = _structure_inputs(snapshot, outcome.document, checkpoint)
            return await self._complete_verified_copy(
                operation_id=operation_id,
                request=request,
                snapshot=snapshot,
                expected=original,
                checkpoint=checkpoint,
                draft=recovered,
            )

        findings = list(outcome.findings)
        if (
            snapshot.markdown is not None
            and request.mode is not TransformMode.UNSLOP
            and self._logo_bytes is None
        ):
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
        if recovered is None:
            if checkpoint.stage is CopyCheckpointStage.CREATING:
                # The preceding create may have committed even when Drive search has
                # not indexed the operation property yet.  Never issue a second create.
                raise DocumentGatewayError(retryable=True)
            if checkpoint.stage is not CopyCheckpointStage.PLANNED:
                raise OutputConflictError
            await self._require_qa_destination_permission(request)
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"stage": CopyCheckpointStage.CREATING})
            )
            draft = await self._create_output(
                operation_id=operation_id,
                request=request,
                snapshot=snapshot,
                source_sha256=source_sha256,
                original=original,
                transformed=outcome.document,
                output_name=checkpoint.output_name,
            )
            await self._record_copy_resource(draft.output.file_id)
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(
                    update={
                        "stage": CopyCheckpointStage.CREATED,
                        "output": draft.output,
                        "output_revision_id": draft.revision_id,
                    }
                )
            )
        else:
            draft = recovered
            if checkpoint.stage not in {
                CopyCheckpointStage.CREATING,
                CopyCheckpointStage.CREATED,
            }:
                raise OutputConflictError
            if checkpoint.stage is CopyCheckpointStage.CREATING:
                await self._record_copy_resource(draft.output.file_id)
                checkpoint = await self._save_copy_checkpoint(
                    checkpoint.model_copy(
                        update={
                            "stage": CopyCheckpointStage.CREATED,
                            "output": draft.output,
                            "output_revision_id": draft.revision_id,
                        }
                    )
                )
        try:
            await self._require_qa_created_copy_permission(
                operation_id=operation_id,
                source_sha256=source_sha256,
                request=request,
                draft=draft,
            )
        except DestinationAccessChangedError:
            await self._record_qa_quarantine(
                checkpoint=checkpoint,
                operation_id=operation_id,
                source_sha256=source_sha256,
                request=request,
                draft=draft,
            )
            raise
        if (
            snapshot.google_document is not None
            and isinstance(request.source, DriveFileSource)
            and request.mode is not TransformMode.UNSLOP
            and self._checkpoint_store is not None
            and self._gateway.structure_provider is not None
        ):
            draft = await self._finish_copy_structure(
                checkpoint=checkpoint, draft=draft, source_id=request.source.file_id
            )
            checkpoint = self._copy_checkpoint_or_none() or checkpoint
            snapshot, original = _structure_inputs(snapshot, outcome.document, checkpoint)
            outcome = replace(outcome, document=original)
            original = _to_ir(snapshot)
        if checkpoint.native_dates is not None:
            draft = replace(draft, native_footer=checkpoint.native_footer)
            draft = await self._finish_copy_dates(
                operation_id=operation_id, checkpoint=checkpoint, draft=draft
            )
            checkpoint = self._copy_checkpoint_or_none() or checkpoint
            prepared_draft = draft
            mutations: list[dict[str, Any]] = []
        elif checkpoint.native_footer is not None:
            draft = await self._finish_copy_footer(
                operation_id=operation_id, checkpoint=checkpoint, draft=draft
            )
            checkpoint = self._copy_checkpoint_or_none() or checkpoint
            prepared_draft = draft
            mutations = []
        elif snapshot.markdown is not None:
            prepared_draft = draft
            mutations = []
        elif request.mode is TransformMode.UNSLOP:
            prepared_draft = draft
            mutations = build_copy_text_mutations(original, outcome.document)
        else:
            prepared_draft = await self._gateway.prepare_brand_shell(
                operation_id=operation_id,
                draft=draft,
                managed_headers=checkpoint.managed_headers,
            )
            if (
                checkpoint.managed_headers is not None
                and draft.applied_mutation_sha256 is None
                and draft.pending_mutation_sha256 is None
                and prepared_draft.revision_id != checkpoint.output_revision_id
            ):
                raise SourceChangedError
            if checkpoint.managed_headers is None:
                # Persist positive ownership before body/header population.
                # Never infer ownership from an already-mutated recovered copy.
                checkpoint = await self._save_copy_checkpoint(
                    checkpoint.model_copy(
                        update={
                            "output_revision_id": prepared_draft.revision_id,
                            "managed_headers": {}
                            if (draft.applied_mutation_sha256 or draft.pending_mutation_sha256)
                            else {
                                str(header.tab_id): str(header.header_id)
                                for header in prepared_draft.headers
                            },
                        }
                    )
                )
            if prepared_draft.logo_uri is None:
                msg = "Brand shell did not provide a logo reference"
                raise RuntimeError(msg)
            findings.extend(
                TransformFinding(code=code, detail=_brand_warning_detail(code))
                for code in prepared_draft.brand_warnings
            )
            mutations = build_copy_mutations(
                original,
                outcome.document,
                profile=self._brand,
                headers=prepared_draft.headers,
                logo_uri=prepared_draft.logo_uri,
                header_title=checkpoint.output_name,
            )
            if outcome.document.copy_font_defended:
                mutations = defend_copy_fonts(
                    mutations,
                    document=snapshot.google_document,
                    original=original,
                    transformed=outcome.document,
                    font_family=self._brand.font_family,
                )
        applied = prepared_draft
        if checkpoint.native_footer is None and checkpoint.native_dates is None:
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
            header_title=checkpoint.output_name,
        )
        applied = replace(applied, revision_id=applied_validation.revision_id)
        if (
            request.mode is not TransformMode.UNSLOP
            and checkpoint.native_footer is None
            and checkpoint.native_dates is None
        ):
            applied = await self._name_copy_tabs(
                operation_id=operation_id,
                checkpoint=checkpoint,
                draft=applied,
                source_has_native_title=any(
                    block.source_named_style == "TITLE" for block in original.blocks
                ),
            )
            checkpoint = self._copy_checkpoint_or_none() or checkpoint
            # Footer metadata follows content and tab verification. Never revisit
            # earlier mutation stages once its durable checkpoint exists.
            if (
                snapshot.markdown is None
                and self._checkpoint_store is not None
                and self._gateway.supports_native_footer
            ):
                applied = await self._finish_copy_footer(
                    operation_id=operation_id, checkpoint=checkpoint, draft=applied
                )
                checkpoint = self._copy_checkpoint_or_none() or checkpoint
        if (
            request.mode is not TransformMode.UNSLOP
            and snapshot.markdown is None
            and self._checkpoint_store is not None
            and self._gateway.supports_native_dates
        ):
            applied = await self._finish_copy_dates(
                operation_id=operation_id, checkpoint=checkpoint, draft=applied
            )
            checkpoint = self._copy_checkpoint_or_none() or checkpoint
        findings = _classify_verified_copy_planner_findings(
            findings,
            planner_finding_count=len(outcome.findings),
            protected_content_preserved=applied_validation.protected_content_preserved,
            structure_preserved=applied_validation.structure_preserved,
        )
        findings = _classify_supported_copy_brand_findings(
            findings,
            supported_brand_applied=applied_validation.supported_brand_applied,
        )
        findings.extend(applied_validation.findings)
        findings = _with_tab_naming_decline(
            findings,
            declined_reason=checkpoint.tab_naming_declined_reason,
        )
        if checkpoint.native_footer is not None and checkpoint.native_footer.coverage is not None:
            findings.extend(
                TransformFinding(
                    code=f"native_footer_{reason}",
                    detail={
                        "custom_footer": "Existing custom footers were preserved on some tabs.",
                        "pageless": "Wiki footers were not added to pageless tabs.",
                        "unsupported_layout": "Wiki footers were not added where tab layout could not be safely verified.",
                    }[reason],
                    severity=FindingSeverity.INFORMATIONAL,
                )
                for reason in sorted(
                    {skip.reason for skip in checkpoint.native_footer.coverage.skipped}
                )
            )
        if checkpoint.native_dates is not None and checkpoint.native_dates.declined_reason:
            findings.append(
                TransformFinding(
                    code="native_date_provider_rejected",
                    detail="Google rejected native date conversion; dates were preserved as text.",
                    severity=FindingSeverity.INFORMATIONAL,
                )
            )
        validation = ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=applied_validation.protected_content_preserved,
            structure_preserved=applied_validation.structure_preserved,
            unsafe_patch_count=sum(
                finding.severity is FindingSeverity.REVIEW
                for finding in findings[: len(outcome.findings)]
            ),
        )
        result = DocumentTransformResult(
            promoted_heading_count=len(checkpoint.authored_labels),
            header_identity=(
                await self._gateway.capture_header_identity(
                    draft=applied,
                    operation_id=operation_id,
                    managed_headers=checkpoint.managed_headers or {},
                    title=checkpoint.output_name,
                )
                if self._gateway.capture_header_identity is not None
                else None
            ),
            operation_id=operation_id,
            source_sha256=source_sha256,
            output=applied.output,
            mode=request.mode,
            applied_patch_count=outcome.applied_count,
            findings=findings,
            validation=validation,
            model_ids=checkpoint.model_ids,
            tool_versions={
                "unslop_catalog_sha256": UNSLOP_CATALOG_SHA256,
                "unslop_commit": UNSLOP_UPSTREAM_COMMIT,
                "unslop_version": UNSLOP_UPSTREAM_VERSION,
            },
            pipeline_version=PIPELINE_VERSION,
            needs_review=_findings_need_review(findings),
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            deterministic_patch_count=outcome.deterministic_count,
            model_patch_count=outcome.model_count,
            llm_invoked=outcome.llm_invoked,
            output_revision_id=applied.revision_id,
            access_scope=(
                "owner_only" if _qa_destination_permission(request) is not None else "inherited"
            ),
        )
        checkpoint = await self._save_copy_checkpoint(
            checkpoint.model_copy(
                update={
                    "stage": CopyCheckpointStage.VERIFIED,
                    "output": applied.output,
                    "output_revision_id": applied.revision_id,
                    "result": result,
                }
            )
        )
        return await self._complete_verified_copy(
            operation_id=operation_id,
            request=request,
            snapshot=snapshot,
            expected=outcome.document,
            checkpoint=checkpoint,
            draft=applied,
        )

    async def _finish_copy_structure(
        self, *, checkpoint: CopyCheckpoint, draft: OutputDraft, source_id: FileId
    ) -> OutputDraft:
        provider = self._gateway.structure_provider
        if provider is None:
            raise OutputVerificationError
        structure = checkpoint.tab_structure
        if structure is None:
            # Do not introduce a new pre-brand stage into a partially completed
            # output, even if a later retry finds that its contents qualify.
            if (
                checkpoint.tab_naming
                or checkpoint.native_footer
                or checkpoint.native_dates
                or draft.applied_mutation_sha256
            ):
                return draft
            structure = await provider.plan(
                document_id=draft.document_id, revision_id=draft.revision_id, source_id=source_id
            )
            if structure is None:
                return draft
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"tab_structure": structure})
            )
        if structure.stage is StructureStage.VERIFIED:
            return draft
        if structure.stage is StructureStage.PLANNED:
            structure, body = prepare_tab_creation(structure)
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"tab_structure": structure})
            )
            await provider.write(draft.document_id, body)
        if structure.stage is StructureStage.CREATING:
            document = await provider.read(draft.document_id)
            structure = recover_created_tabs(structure, document)
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"tab_structure": structure})
            )
        if structure.stage is StructureStage.CREATED:
            structure, body = prepare_content_move(structure)
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"tab_structure": structure})
            )
            await provider.write(draft.document_id, body)
        document = await provider.read(draft.document_id)
        if not verify_structure_content(structure, document):
            raise OutputVerificationError
        revision = DocsRevisionId(document["revisionId"])
        structure = replace(structure, stage=StructureStage.VERIFIED, revision_id=revision)
        await self._save_copy_checkpoint(checkpoint.model_copy(update={"tab_structure": structure}))
        return replace(draft, revision_id=revision)

    async def _finish_copy_dates(
        self, *, operation_id: uuid.UUID, checkpoint: CopyCheckpoint, draft: OutputDraft
    ) -> OutputDraft:
        dates = checkpoint.native_dates
        if dates is None:
            if (
                dates := await self._gateway.plan_copy_dates(draft=draft, operation_id=operation_id)
            ) is None:
                return draft
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"native_dates": dates})
            )
        dates = await self._gateway.apply_copy_dates(
            document_id=draft.document_id, checkpoint=dates
        )
        if not dates.complete and dates.declined_reason is None:
            raise OutputVerificationError
        await self._save_copy_checkpoint(checkpoint.model_copy(update={"native_dates": dates}))
        return replace(draft, native_dates=dates, revision_id=dates.revision_id)

    async def _finish_copy_footer(
        self,
        *,
        operation_id: uuid.UUID,
        checkpoint: CopyCheckpoint,
        draft: OutputDraft,
    ) -> OutputDraft:
        footer = checkpoint.native_footer
        if footer is None:
            if (
                footer := await self._gateway.plan_copy_footer(
                    draft=draft, operation_id=operation_id
                )
            ) is None:
                return draft
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"native_footer": footer})
            )
        # Two native batches and one persisted transition per bounded tab.
        # Legacy checkpoints never acquire coverage or additional tabs on retry.
        for _ in range(3 * MAX_DOC_TABS):
            if footer_coverage_complete(footer):
                break
            footer = await self._gateway.apply_copy_footer_stage(
                document_id=draft.document_id, checkpoint=footer
            )
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"native_footer": footer})
            )
        if not footer_coverage_complete(footer):
            raise OutputVerificationError
        return replace(draft, revision_id=footer.revision_id, native_footer=footer)

    async def _name_copy_tabs(
        self,
        *,
        operation_id: uuid.UUID,
        checkpoint: CopyCheckpoint,
        draft: OutputDraft,
        source_has_native_title: bool,
    ) -> OutputDraft:
        if checkpoint.tab_naming_declined_reason is not None:
            return draft
        if checkpoint.tab_naming is None:
            try:
                tab_naming = await self._gateway.plan_tab_naming(
                    document_id=draft.document_id,
                    title=checkpoint.source_title,
                    operation_id=operation_id,
                    required_revision_id=draft.revision_id,
                    prefer_document_title=bool(checkpoint.authored_labels)
                    and not source_has_native_title,
                )
            except UnsupportedTabMetadataError:
                await self._save_copy_checkpoint(
                    checkpoint.model_copy(
                        update={"tab_naming_declined_reason": "unsupported_metadata"}
                    )
                )
                return draft
            if tab_naming is None:
                return draft
            checkpoint = await self._save_copy_checkpoint(
                checkpoint.model_copy(update={"tab_naming": tab_naming})
            )
        naming = checkpoint.tab_naming
        if naming is None:
            raise DocumentGatewayError
        revision = await self._gateway.apply_tab_naming(
            document_id=draft.document_id,
            checkpoint=naming,
        )
        return replace(draft, revision_id=revision)

    def _copy_checkpoint_or_none(self) -> CopyCheckpoint | None:
        checkpoint = self._checkpoint
        if checkpoint is None:
            return None
        if not isinstance(checkpoint, CopyCheckpoint):
            raise OutputConflictError
        return checkpoint

    async def _save_copy_checkpoint(self, checkpoint: CopyCheckpoint) -> CopyCheckpoint:
        if self._checkpoint_store is None:
            self._checkpoint = checkpoint
            return checkpoint
        if self._task_id is None or self._lease_token is None:
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

    async def _record_copy_resource(self, resource_id: FileId) -> None:
        if self._checkpoint_store is None:
            return
        if self._task_id is None or self._lease_token is None:
            raise DocumentGatewayError
        await self._checkpoint_store.record_resource(
            self._task_id,
            lease_token=self._lease_token,
            resource_id=resource_id,
        )

    async def _complete_verified_copy(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        expected: DocumentIR,
        checkpoint: CopyCheckpoint,
        draft: OutputDraft,
    ) -> DocumentTransformResult:
        result = checkpoint.result
        draft = replace(
            draft, native_footer=checkpoint.native_footer, native_dates=checkpoint.native_dates
        )
        if (
            checkpoint.stage not in {CopyCheckpointStage.VERIFIED, CopyCheckpointStage.ATTESTED}
            or checkpoint.output is None
            or checkpoint.output_revision_id is None
            or result is None
            or draft.output.file_id != checkpoint.output.file_id
        ):
            raise OutputConflictError
        await self._require_unchanged_drive_source(request, snapshot)
        verification = await self._gateway.verify_output(
            draft=draft,
            source=snapshot,
            expected=expected,
            verify_brand=request.mode is not TransformMode.UNSLOP,
            header_title=checkpoint.output_name,
        )
        revision_id = verification.revision_id or draft.revision_id
        if (
            not verification.content_preserved
            or not verification.structure_preserved
            or revision_id != checkpoint.output_revision_id
        ):
            raise OutputVerificationError
        serialized_result = _public_result_payload(result)
        expected_result_sha256 = _json_sha256(serialized_result)
        expected_revision_sha256 = hashlib.sha256(revision_id.encode()).hexdigest()
        if not draft.completed:
            await self._gateway.complete(
                operation_id=operation_id,
                draft=replace(draft, revision_id=revision_id),
                result=serialized_result,
            )
            recovered = await self._gateway.recover_output(operation_id)
            if recovered is None or recovered.output.file_id != draft.output.file_id:
                raise OutputConflictError
            draft = replace(
                recovered,
                native_footer=checkpoint.native_footer,
                native_dates=checkpoint.native_dates,
            )
        durable = self._checkpoint_store is not None
        digest_matches = (
            draft.result_sha256 == expected_result_sha256
            and draft.verified_revision_sha256 == expected_revision_sha256
        )
        legacy_fake_matches = draft.result == serialized_result
        if not draft.completed or (not digest_matches and (durable or not legacy_fake_matches)):
            raise OutputConflictError
        final_verification = await self._gateway.verify_output(
            draft=draft,
            source=snapshot,
            expected=expected,
            verify_brand=request.mode is not TransformMode.UNSLOP,
            header_title=checkpoint.output_name,
        )
        final_revision = final_verification.revision_id or draft.revision_id
        if (
            not final_verification.content_preserved
            or not final_verification.structure_preserved
            or final_revision != checkpoint.output_revision_id
        ):
            raise OutputVerificationError
        attested = await self._attest_completed_qa_copy(
            operation_id=operation_id,
            request=request,
            snapshot=snapshot,
            expected=expected,
            terminal_verification=final_verification,
            checkpoint=checkpoint,
            draft=replace(draft, revision_id=final_revision),
            result=result,
        )
        result, checkpoint = attested.result, attested.checkpoint
        if result.header_identity is not None:
            await self._save_copy_checkpoint(
                checkpoint.model_copy(
                    update={
                        "header_finalization": HeaderIdentityFinalization(
                            result_sha256=expected_result_sha256,
                            verified_revision_sha256=expected_revision_sha256,
                        )
                    }
                )
            )
        return result

    async def _attest_completed_qa_copy(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        expected: DocumentIR,
        terminal_verification: OutputVerification,
        checkpoint: CopyCheckpoint,
        draft: OutputDraft,
        result: DocumentTransformResult,
    ) -> AttestedCopy:
        if _qa_destination_permission(request) is None:
            return AttestedCopy(result=result, checkpoint=checkpoint)
        try:
            permission_evidence = await self._require_qa_created_copy_permission(
                operation_id=operation_id,
                source_sha256=result.source_sha256,
                request=request,
                draft=draft,
            )
        except DestinationAccessChangedError:
            await self._record_qa_quarantine(
                checkpoint=checkpoint,
                operation_id=operation_id,
                source_sha256=result.source_sha256,
                request=request,
                draft=draft,
            )
            raise
        if permission_evidence is None:  # pragma: no cover - internal binding invariant
            raise DestinationAccessChangedError
        post_acl_verification = await self._gateway.verify_output(
            draft=draft,
            source=snapshot,
            expected=expected,
            verify_brand=request.mode is not TransformMode.UNSLOP,
            header_title=checkpoint.output_name,
        )
        post_acl_revision = post_acl_verification.revision_id or draft.revision_id
        verification_failed = (
            not post_acl_verification.content_preserved
            or not post_acl_verification.structure_preserved
        )
        revision_changed = (
            post_acl_revision != draft.revision_id or post_acl_revision != result.output_revision_id
        )
        brand_changed = (
            post_acl_verification.brand_applied != terminal_verification.brand_applied
            or post_acl_verification.supported_brand_applied
            != terminal_verification.supported_brand_applied
        )
        if verification_failed or revision_changed or brand_changed:
            await self._record_qa_quarantine(
                checkpoint=checkpoint,
                operation_id=operation_id,
                source_sha256=result.source_sha256,
                request=request,
                draft=replace(draft, revision_id=post_acl_revision),
            )
            raise OutputVerificationError
        attested = QaDocumentTransformResult.model_validate(
            _public_result_payload(result)
            | {
                "access_scope": "owner_only",
                "qa_permission_evidence": permission_evidence,
            }
        )
        saved = await self._save_copy_checkpoint(
            CopyCheckpoint.model_validate(
                checkpoint.model_dump(mode="python")
                | {
                    "stage": CopyCheckpointStage.ATTESTED,
                    "output": draft.output,
                    "output_revision_id": draft.revision_id,
                    "result": attested,
                    "qa_permission_evidence": permission_evidence,
                    "qa_quarantine": None,
                }
            )
        )
        return AttestedCopy(result=attested, checkpoint=saved)

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
        if checkpoint is not None and not isinstance(checkpoint, InPlaceCheckpoint):
            raise OutputConflictError
        if checkpoint is None and snapshot.modified_time != request.source.expected_modified_time:
            raise SourceChangedError
        if checkpoint is not None:
            if checkpoint.document_id != request.source.file_id:
                raise OutputConflictError
            if checkpoint.pagination != existing_native_pagination(
                snapshot.google_document, _to_ir(snapshot)
            ):
                raise InPlaceNeedsReviewError
            checkpoint_contract = (
                checkpoint.source_title,
                checkpoint.source_modified_time,
                checkpoint.brand_profile_id,
                checkpoint.brand_profile_sha256,
                checkpoint.pipeline_version,
                checkpoint.request_builder_version,
                checkpoint.unslop_catalog_sha256,
            )
            current_contract = (
                snapshot.title,
                request.source.expected_modified_time,
                CURRENT_BRAND_MANIFEST.profile_id,
                CURRENT_BRAND_MANIFEST.sha256,
                PIPELINE_VERSION,
                IN_PLACE_REQUEST_BUILDER_VERSION,
                UNSLOP_CATALOG_SHA256,
            )
            if checkpoint_contract != current_contract:
                raise OutputConflictError
            if checkpoint.table_font_plan is not None:
                if request.mode is not TransformMode.FORMAT or checkpoint.replacements:
                    raise InPlaceNeedsReviewError
                if snapshot.google_document is None:
                    raise InPlaceNeedsReviewError
                try:
                    project_table_fonts(
                        snapshot.google_document,
                        checkpoint.table_font_plan,
                        applied=checkpoint.stage is InPlaceCheckpointStage.COMPLETE,
                    )
                except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
                    raise InPlaceNeedsReviewError from error
            elif any(finding.code == "table_font_scope" for finding in checkpoint.findings):
                raise InPlaceNeedsReviewError
            if _claims_complex_structure_safe_noop(
                checkpoint
            ) and not _is_complex_structure_safe_noop(checkpoint):
                raise InPlaceNeedsReviewError
            if {item.block_id for item in checkpoint.paragraph_layout} != {
                finding.block_id
                for finding in checkpoint.findings
                if finding.code == "native_paragraph_layout_preserved"
            } or not verify_paragraph_layout(
                snapshot.google_document,
                checkpoint.paragraph_layout,
                applied=(
                    not _is_complex_structure_safe_noop(checkpoint)
                    if checkpoint.stage is InPlaceCheckpointStage.COMPLETE
                    else None
                ),
            ):
                raise InPlaceNeedsReviewError
            if checkpoint.native_font_proof is None and (
                checkpoint.preserved_font_block_ids
                or any(
                    finding.code == "native_rich_text_preserved" for finding in checkpoint.findings
                )
            ):
                raise InPlaceNeedsReviewError
            if set(checkpoint.preserved_font_block_ids) != {
                finding.block_id
                for finding in checkpoint.findings
                if finding.code == "native_font_family_preserved"
            }:
                raise InPlaceNeedsReviewError
            if len(checkpoint.visual_authority_block_ids) != len(
                set(checkpoint.visual_authority_block_ids)
            ):
                raise InPlaceNeedsReviewError
            if (
                set(checkpoint.visual_authority_block_ids)
                != {
                    finding.block_id
                    for finding in checkpoint.findings
                    if finding.code == "native_visual_authority_preserved"
                }
                or set(checkpoint.visual_authority_block_ids)
                != {
                    proof.block_id
                    for proof in checkpoint.native_font_proof or ()
                    if proof.visual_authority
                }
                or not set(checkpoint.visual_authority_block_ids).isdisjoint(
                    checkpoint.replacements
                )
            ):
                raise InPlaceNeedsReviewError
            if checkpoint.native_font_proof is not None and not verify_native_font_proof(
                snapshot.google_document,
                checkpoint.native_font_proof,
                preserved_block_ids=_preserved_block_ids(checkpoint),
                diagnostic_stage="resume",
                font_policy=NativeFontVerificationPolicy(
                    require_target_family=(
                        checkpoint.stage is InPlaceCheckpointStage.COMPLETE
                        and not _is_complex_structure_safe_noop(checkpoint)
                    ),
                    preserved_family_block_ids=checkpoint.preserved_font_block_ids,
                ),
            ):
                raise InPlaceNeedsReviewError
            if checkpoint.stage is InPlaceCheckpointStage.COMPLETE:
                if snapshot.revision_id != checkpoint.current_revision_id:
                    raise InPlaceNeedsReviewError
                if _is_complex_structure_safe_noop(checkpoint):
                    if not _matches_complex_structure_safe_noop(
                        snapshot,
                        await self._gateway.inspect_in_place(
                            snapshot=snapshot, title=snapshot.title
                        ),
                        checkpoint,
                    ):
                        raise InPlaceNeedsReviewError
                elif checkpoint.custom_furniture:
                    inspection = await self._gateway.inspect_in_place(
                        snapshot=snapshot, title=snapshot.title
                    )
                    if inspection.custom_furniture != checkpoint.custom_furniture or (
                        checkpoint.custom_header_titles_required
                        and inspection.custom_header_titles_present
                        != checkpoint.custom_header_titles
                    ):
                        raise InPlaceNeedsReviewError
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
        inspection = await self._gateway.inspect_in_place(
            snapshot=snapshot,
            title=snapshot.title,
            **(
                {"table_font_plan": checkpoint.table_font_plan}
                if checkpoint is not None and checkpoint.table_font_plan is not None
                else {}
            ),
        )
        if checkpoint is not None and checkpoint.custom_furniture != inspection.custom_furniture:
            raise InPlaceNeedsReviewError
        if checkpoint is not None and checkpoint.header_identity != inspection.header_identity:
            raise OutputConflictError
        if checkpoint is None:
            checkpoint = await self._plan_initial_in_place(
                operation_id=operation_id,
                request=request,
                snapshot=snapshot,
                inspection=inspection,
            )
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

        if (
            checkpoint.stage is InPlaceCheckpointStage.HEADERS
            and snapshot.revision_id != checkpoint.current_revision_id
            and _matches_pre_header_checkpoint(snapshot, inspection, checkpoint)
        ):
            checkpoint = await self._save_checkpoint(
                checkpoint.model_copy(update={"current_revision_id": snapshot.revision_id})
            )

        if checkpoint.stage is InPlaceCheckpointStage.HEADERS:
            stage_result = recover_empty_header_postcondition(
                snapshot, inspection, checkpoint, operation_id
            )
            if stage_result is None:
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
                shared_drive_id=snapshot.shared_drive_id,
                web_url=snapshot.web_url,
            )
            try:
                inspection = await self._gateway.inspect_in_place(
                    snapshot=stage_snapshot,
                    title=snapshot.title,
                    **(
                        {"table_font_plan": checkpoint.table_font_plan}
                        if checkpoint.table_font_plan is not None
                        else {}
                    ),
                )
            except InPlaceNeedsReviewError as exc:
                raise InPlacePostMutationVerificationError from exc
            header_expected = _to_ir(stage_snapshot).replace_blocks({})
            try:
                header_verification = await self._gateway.verify_in_place(
                    document=stage_result.document,
                    title=snapshot.title,
                    expected=header_expected,
                    structural_sha256=checkpoint.structural_sha256,
                    protected_structure_sha256=checkpoint.protected_structure_sha256,
                    verify_brand=False,
                    custom_furniture=checkpoint.custom_furniture,
                    custom_header_titles=checkpoint.custom_header_titles,
                    table_font_plan=checkpoint.table_font_plan,
                )
            except InPlaceNeedsReviewError as exc:
                raise InPlacePostMutationVerificationError from exc
            if not (
                header_verification.content_preserved and header_verification.structure_preserved
            ):
                raise InPlacePostMutationVerificationError
            if checkpoint.native_font_proof is not None and not verify_native_font_proof(
                stage_result.document,
                checkpoint.native_font_proof,
                preserved_block_ids=_preserved_block_ids(checkpoint),
                diagnostic_stage="headers",
                font_policy=NativeFontVerificationPolicy(
                    preserved_family_block_ids=checkpoint.preserved_font_block_ids,
                ),
            ):
                raise InPlacePostMutationVerificationError
            if not verify_paragraph_layout(
                stage_result.document, checkpoint.paragraph_layout, applied=False
            ):
                raise InPlacePostMutationVerificationError
            if (
                header_expected.semantic_sha256 != checkpoint.original_semantic_sha256
                or inspection.body_sha256 != checkpoint.body_sha256
                or inspection.header_safe_sha256 != checkpoint.header_safe_sha256
            ):
                raise InPlacePostMutationVerificationError
            if inspection.missing_header_tab_ids or inspection.unknown_header_tab_ids:
                raise InPlacePostMutationVerificationError
            if inspection.custom_furniture != checkpoint.custom_furniture:
                raise InPlacePostMutationVerificationError
            snapshot = stage_snapshot
            original = apply_paragraph_layout(
                apply_native_pagination(_to_ir(stage_snapshot), checkpoint.pagination),
                checkpoint.paragraph_layout,
            )
            original = apply_visual_authority(original, checkpoint.visual_authority_block_ids)
            original = apply_native_font_preservation(original, checkpoint.preserved_font_block_ids)
            transformed = original.replace_blocks(checkpoint.replacements)
            requests = self._content_requests(
                request=request,
                snapshot=stage_snapshot,
                original=original,
                transformed=transformed,
                inspection=inspection,
            )
            if checkpoint.table_font_plan is not None:
                requests.extend(table_font_requests(checkpoint.table_font_plan))
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
            checkpoint = await self._name_in_place_tabs(
                operation_id=operation_id,
                request=request,
                checkpoint=checkpoint,
            )
            checkpoint = await self._save_checkpoint(
                checkpoint.model_copy(update={"stage": InPlaceCheckpointStage.COMPLETE})
            )
            return self._in_place_result(
                operation_id=operation_id,
                request=request,
                checkpoint=checkpoint,
                output=inspection.output,
            )
        before_content = apply_native_font_preservation(
            apply_visual_authority(
                apply_paragraph_layout(_to_ir(snapshot), checkpoint.paragraph_layout),
                checkpoint.visual_authority_block_ids,
            ),
            checkpoint.preserved_font_block_ids,
        )
        expected_content = apply_native_pagination(
            before_content.replace_blocks(checkpoint.replacements), checkpoint.pagination
        )
        if expected_content.semantic_sha256 != checkpoint.expected_semantic_sha256:
            raise InPlaceNeedsReviewError
        if checkpoint.tab_naming is not None or checkpoint.tab_naming_declined_reason is not None:
            if snapshot.google_document is None or snapshot.revision_id is None:
                raise InPlaceNeedsReviewError
            stage_result = InPlaceStageResult(snapshot.revision_id, snapshot.google_document)
        else:
            stage_result = await self._apply_checkpoint_stage(checkpoint)
        try:
            verification = await self._gateway.verify_in_place(
                document=stage_result.document,
                title=snapshot.title,
                expected=expected_content,
                structural_sha256=checkpoint.structural_sha256,
                protected_structure_sha256=checkpoint.protected_structure_sha256,
                verify_brand=request.mode is not TransformMode.UNSLOP,
                custom_furniture=checkpoint.custom_furniture,
                custom_header_titles=checkpoint.custom_header_titles,
                table_font_plan=checkpoint.table_font_plan,
            )
        except InPlaceNeedsReviewError as exc:
            raise InPlacePostMutationVerificationError from exc
        if checkpoint.table_font_plan is not None and not table_font_proof_matches(
            stage_result.document, checkpoint.table_font_plan, applied=True
        ):
            raise InPlacePostMutationVerificationError
        if not (
            verification.content_preserved
            and verification.structure_preserved
            and (verification.brand_applied or verification.supported_brand_applied)
        ):
            raise InPlacePostMutationVerificationError
        if not verify_paragraph_layout(
            stage_result.document, checkpoint.paragraph_layout, applied=True
        ):
            raise InPlacePostMutationVerificationError
        if checkpoint.native_font_proof is not None and not verify_native_font_proof(
            stage_result.document,
            checkpoint.native_font_proof,
            preserved_block_ids=_preserved_block_ids(checkpoint),
            diagnostic_stage="content",
            font_policy=NativeFontVerificationPolicy(
                require_target_family=True,
                preserved_family_block_ids=checkpoint.preserved_font_block_ids,
            ),
        ):
            raise InPlacePostMutationVerificationError
        checkpoint = checkpoint.model_copy(update={"current_revision_id": stage_result.revision_id})
        checkpoint = await self._name_in_place_tabs(
            operation_id=operation_id,
            request=request,
            checkpoint=checkpoint,
        )
        checkpoint = await self._save_checkpoint(
            checkpoint.model_copy(
                update={
                    "stage": InPlaceCheckpointStage.COMPLETE,
                    "current_revision_id": checkpoint.current_revision_id,
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

    async def _name_in_place_tabs(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        checkpoint: InPlaceCheckpoint,
    ) -> InPlaceCheckpoint:
        if request.mode is TransformMode.UNSLOP:
            return checkpoint
        if checkpoint.tab_naming_declined_reason is not None:
            return checkpoint
        if checkpoint.tab_naming is None:
            try:
                tab_naming = await self._gateway.plan_tab_naming(
                    document_id=checkpoint.document_id,
                    title=checkpoint.source_title,
                    operation_id=operation_id,
                    required_revision_id=checkpoint.current_revision_id,
                )
            except UnsupportedTabMetadataError:
                return await self._save_checkpoint(
                    checkpoint.model_copy(
                        update={"tab_naming_declined_reason": "unsupported_metadata"}
                    )
                )
            if tab_naming is None:
                return checkpoint
            checkpoint = await self._save_checkpoint(
                checkpoint.model_copy(update={"tab_naming": tab_naming, "changed": True})
            )
        naming = checkpoint.tab_naming
        if naming is None:
            raise DocumentGatewayError
        revision = await self._gateway.apply_tab_naming(
            document_id=checkpoint.document_id,
            checkpoint=naming,
        )
        return checkpoint.model_copy(update={"current_revision_id": revision})

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
        pagination = existing_native_pagination(snapshot.google_document, original)
        original = apply_native_pagination(original, pagination)
        visual_ids = (
            visual_authority_block_ids(snapshot.google_document, original)
            if request.mode is not TransformMode.UNSLOP
            else ()
        )
        original = apply_visual_authority(original, visual_ids)
        outcome = await self._planner.transform(
            apply_visual_authority(original, visual_ids, planner=True), request
        )
        outcome = replace(
            outcome,
            document=replace(
                outcome.document,
                blocks=tuple(
                    source_block if block.id in visual_ids else block
                    for source_block, block in zip(
                        original.blocks, outcome.document.blocks, strict=True
                    )
                ),
            ),
            findings=tuple(
                finding
                for finding in outcome.findings
                if not (
                    finding.block_id in visual_ids
                    and finding.code == "google_doc_text_target_unsupported"
                )
            ),
        )
        try:
            paragraph_layout = (
                capture_paragraph_layout(snapshot.google_document, original, outcome.document)
                if request.mode is not TransformMode.UNSLOP
                else ()
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            raise InPlaceNeedsReviewError from error
        original = apply_paragraph_layout(original, paragraph_layout)
        outcome = replace(
            outcome, document=apply_paragraph_layout(outcome.document, paragraph_layout)
        )
        if pagination:
            outcome = replace(
                outcome, document=apply_native_pagination(outcome.document, pagination)
            )
        preserved_font_ids = (
            preserved_native_font_blocks(snapshot.google_document, original, outcome.document)
            if request.mode is not TransformMode.UNSLOP
            else ()
        )
        preserved_font_ids = tuple(dict.fromkeys((*preserved_font_ids, *visual_ids)))
        original = apply_native_font_preservation(original, preserved_font_ids)
        outcome = replace(
            outcome, document=apply_native_font_preservation(outcome.document, preserved_font_ids)
        )
        _preflight_native_mutation_budget(
            request=request,
            snapshot=snapshot,
            original=original,
            transformed=outcome.document,
            brand=self._brand,
        )
        preserved_findings = preserved_native_findings(
            original=original,
            transformed=outcome.document,
            findings=outcome.findings,
            native_document=snapshot.google_document,
            mode=request.mode,
        )
        branded = request.mode is not TransformMode.UNSLOP
        if branded and inspection.unknown_header_tab_ids:
            logger.info(
                "document in-place preflight checks={checks}",
                checks={
                    "in_place_unknown_header": True,
                },
            )
            raise CustomHeaderNeedsReviewError
        if branded and not inspection.logo_uri:
            logger.info(
                "document in-place preflight checks={checks}",
                checks={"in_place_missing_logo": True},
            )
            raise BrandAssetUnavailableError
        replacements = {
            block.id: transformed.text
            for block, transformed in zip(original.blocks, outcome.document.blocks, strict=True)
            if block.text != transformed.text
        }
        if pagination and (
            replacements
            or (branded and not (inspection.fully_branded or inspection.supported_brand_applied))
        ):
            # Existing-only recognition is a no-op contract, not authority to
            # reinterpret a partially edited layout or move its spacer ranges.
            raise InPlaceNeedsReviewError
        if (
            branded
            and (inspection.fully_branded or inspection.supported_brand_applied)
            and not replacements
        ):
            requests = []
            stage = InPlaceCheckpointStage.CONTENT
        elif branded and inspection.missing_header_tab_ids:
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
                snapshot=snapshot,
                original=original,
                transformed=outcome.document,
                inspection=inspection,
            )
            stage = InPlaceCheckpointStage.CONTENT
        table_scope = await self._table_font_scope(
            request=request,
            snapshot=snapshot,
            original=original,
            transformed=outcome.document,
            inspection=inspection,
        )
        if stage is InPlaceCheckpointStage.CONTENT:
            requests.extend(table_scope.requests)
        if requests and inspection.marker_range is None:
            logger.info(
                "document in-place preflight checks={checks}",
                checks={"in_place_missing_marker": True},
            )
            raise InPlaceNeedsReviewError
        try:
            native_font_proof = capture_native_font_proof(
                snapshot.google_document,
                original,
                transformed=outcome.document,
                target_font_family=self._brand.font_family if branded else None,
                preserved_family_block_ids=preserved_font_ids,
                visual_authority=NativeVisualAuthoritySelection(visual_ids),
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            raise InPlaceNeedsReviewError from error
        if preserved_findings is None:
            return await self._complete_complex_structure_safe_noop(
                source=source,
                snapshot=snapshot,
                inspection=inspection,
                original=original,
                outcome=outcome,
                pagination=pagination,
                visual_ids=visual_ids,
                preserved_font_ids=preserved_font_ids,
                branded=branded,
            )
        checkpoint = InPlaceCheckpoint(
            visual_authority_block_ids=tuple(NativeFontBlockId(item) for item in visual_ids),
            preserved_font_block_ids=tuple(NativeFontBlockId(item) for item in preserved_font_ids),
            paragraph_layout=paragraph_layout,
            table_font_plan=table_scope.plan,
            native_font_proof=native_font_proof,
            custom_furniture=inspection.custom_furniture,
            custom_header_titles=tuple(
                dict.fromkeys(
                    (
                        *inspection.custom_header_titles_present,
                        *inspection.custom_header_title_targets,
                    )
                )
            )
            if branded
            else (),
            custom_header_title_targets=inspection.custom_header_title_targets if branded else (),
            custom_header_titles_required=branded
            and bool(any(binding.header_ids for binding in inspection.custom_furniture)),
            pagination=pagination,
            header_identity=inspection.header_identity,
            document_id=source.file_id,
            source_title=snapshot.title,
            source_sha256=_source_sha256(snapshot, original),
            source_modified_time=source.expected_modified_time,
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            pipeline_version=PIPELINE_VERSION,
            request_builder_version=IN_PLACE_REQUEST_BUILDER_VERSION,
            unslop_catalog_sha256=UNSLOP_CATALOG_SHA256,
            original_semantic_sha256=original.semantic_sha256,
            expected_semantic_sha256=outcome.document.semantic_sha256,
            structural_sha256=inspection.structural_sha256,
            protected_structure_sha256=table_scope.protected_sha256,
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
            findings=list(preserved_findings)
            + [
                TransformFinding(
                    code="native_visual_authority_preserved",
                    block_id=block_id,
                    detail="Authored title size, color, alignment, font family, and emphasis were preserved unchanged; wording cleanup was skipped for this paragraph. Full Sarj typography was not applied.",
                    severity=FindingSeverity.INFORMATIONAL,
                )
                for block_id in visual_ids
            ]
            + [
                TransformFinding(
                    code="native_font_family_preserved",
                    block_id=block_id,
                    detail="This paragraph's font family and authored styling were preserved unchanged because a safe, bounded font change could not be proved.",
                    severity=FindingSeverity.INFORMATIONAL,
                )
                for block_id in preserved_font_ids
            ]
            + [
                TransformFinding(
                    code="native_paragraph_layout_preserved",
                    block_id=item.block_id,
                    detail="Authored non-start paragraph alignment, spacing, and layout were preserved; supported typography was checked. Full Sarj paragraph layout was not applied.",
                    severity=FindingSeverity.INFORMATIONAL,
                )
                for item in paragraph_layout
            ]
            + table_scope.findings
            + (
                [
                    TransformFinding(
                        code="custom_furniture_preserved",
                        detail="Supported body formatting was applied; custom headers, footers, and page geometry were preserved. Full Sarj branding was not applied.",
                        severity=FindingSeverity.INFORMATIONAL,
                    )
                ]
                if branded and inspection.custom_furniture and not inspection.fully_branded
                else []
            ),
            applied_patch_count=outcome.applied_count,
            deterministic_patch_count=outcome.deterministic_count,
            model_patch_count=outcome.model_count,
            llm_invoked=outcome.llm_invoked,
            model_ids=[ModelId(value) for value in self._generator.model_ids]
            if outcome.llm_invoked
            else [],
            headers={str(header.tab_id): str(header.header_id) for header in inspection.headers},
            preserved_headers={
                str(header.tab_id): str(header.header_id) for header in inspection.preserved_headers
            },
            changed=bool(requests),
        )
        return await self._save_checkpoint(checkpoint)

    async def _complete_complex_structure_safe_noop(
        self,
        *,
        source: DriveFileSource,
        snapshot: SourceSnapshot,
        inspection: InPlaceInspection,
        original: DocumentIR,
        outcome: TransformOutcome,
        pagination: tuple[NativePaginationRule, ...],
        visual_ids: tuple[str, ...],
        preserved_font_ids: tuple[str, ...],
        branded: bool,
    ) -> InPlaceCheckpoint:
        if snapshot.revision_id is None:
            raise InPlaceNeedsReviewError
        try:
            paragraph_layout = capture_paragraph_layout(
                snapshot.google_document, original, original
            )
            native_font_proof = capture_native_font_proof(
                snapshot.google_document,
                original,
                target_font_family=self._brand.font_family if branded else None,
                transformed=original,
                preserved_family_block_ids=preserved_font_ids,
                visual_authority=NativeVisualAuthoritySelection(visual_ids),
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            raise InPlaceNeedsReviewError from error
        final_snapshot = await self._gateway.load_drive_source(source)
        final_inspection = await self._gateway.inspect_in_place(
            snapshot=final_snapshot, title=final_snapshot.title
        )
        checkpoint = InPlaceCheckpoint(
            visual_authority_block_ids=tuple(NativeFontBlockId(item) for item in visual_ids),
            preserved_font_block_ids=tuple(NativeFontBlockId(item) for item in preserved_font_ids),
            paragraph_layout=paragraph_layout,
            native_font_proof=native_font_proof,
            custom_furniture=inspection.custom_furniture,
            custom_header_titles=inspection.custom_header_titles_present if branded else (),
            custom_header_title_targets=(),
            custom_header_titles_required=branded
            and bool(any(binding.header_ids for binding in inspection.custom_furniture)),
            pagination=pagination,
            header_identity=inspection.header_identity,
            document_id=source.file_id,
            source_title=snapshot.title,
            source_sha256=_source_sha256(snapshot, original),
            source_modified_time=source.expected_modified_time,
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            pipeline_version=PIPELINE_VERSION,
            request_builder_version=IN_PLACE_REQUEST_BUILDER_VERSION,
            unslop_catalog_sha256=UNSLOP_CATALOG_SHA256,
            original_semantic_sha256=original.semantic_sha256,
            expected_semantic_sha256=original.semantic_sha256,
            structural_sha256=inspection.structural_sha256,
            protected_structure_sha256=inspection.protected_structure_sha256,
            body_sha256=inspection.body_sha256,
            header_safe_sha256=inspection.header_safe_sha256,
            source_revision_id=snapshot.revision_id,
            current_revision_id=snapshot.revision_id,
            stage=InPlaceCheckpointStage.COMPLETE,
            replacements={},
            findings=_complex_structure_safe_noop_findings(
                visual_ids=visual_ids,
                preserved_font_ids=preserved_font_ids,
                paragraph_layout=paragraph_layout,
            ),
            applied_patch_count=0,
            deterministic_patch_count=0,
            model_patch_count=0,
            llm_invoked=outcome.llm_invoked,
            model_ids=[ModelId(value) for value in self._generator.model_ids]
            if outcome.llm_invoked
            else [],
            headers={str(header.tab_id): str(header.header_id) for header in inspection.headers},
            preserved_headers={
                str(header.tab_id): str(header.header_id) for header in inspection.preserved_headers
            },
            changed=False,
        )
        if final_inspection != inspection or not _matches_complex_structure_safe_noop(
            final_snapshot, final_inspection, checkpoint
        ):
            raise InPlaceNeedsReviewError
        logger.info(
            "document in-place preflight checks={checks}",
            checks={"in_place_complex_structure_preserved": True},
        )
        return await self._save_checkpoint(checkpoint)

    @staticmethod
    async def _table_font_scope(
        *,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        original: DocumentIR,
        transformed: DocumentIR,
        inspection: InPlaceInspection,
    ) -> _TableFontScope:
        del original, transformed
        empty = _TableFontScope(None, inspection.protected_structure_sha256, [], [])
        if request.mode is TransformMode.UNSLOP or snapshot.google_document is None:
            return empty
        try:
            candidate = plan_table_fonts(snapshot.google_document)
        except KeyError, IndexError, TypeError, ValueError, AttributeError, SourceTooLargeError:
            return empty._replace(
                findings=[
                    TransformFinding(
                        code="table_font_preserved",
                        detail="Table typography was preserved unchanged because its safety proof or operation budget could not be established.",
                        severity=FindingSeverity.INFORMATIONAL,
                    )
                ]
            )
        if candidate.targets or candidate.skipped_paragraphs:
            return empty._replace(
                findings=[
                    TransformFinding(
                        code="table_font_preserved",
                        detail="Table typography and geometry were preserved unchanged because bold and weight preservation for native table font updates has not been verified.",
                        severity=FindingSeverity.INFORMATIONAL,
                    )
                ]
            )
        return empty

    def _content_requests(
        self,
        *,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        original: DocumentIR,
        transformed: DocumentIR,
        inspection: InPlaceInspection,
    ) -> list[dict[str, Any]]:
        if request.mode is TransformMode.UNSLOP:
            return build_copy_text_mutations(original, transformed)
        requests = build_copy_mutations(
            original,
            transformed,
            profile=self._brand,
            headers=inspection.headers,
            logo_uri=inspection.logo_uri,
        )
        requests.extend(
            build_custom_header_title_requests(
                snapshot.title,
                profile=self._brand,
                headers=inspection.custom_header_title_targets,
            )
        )
        preserved_tabs = {binding.tab_id for binding in inspection.custom_furniture}
        requests = filter_redundant_native_fonts(
            requests,
            document=snapshot.google_document,
            original=original,
            transformed=transformed,
            font_family=self._brand.font_family,
        )
        requests = defend_native_font_requests(
            requests, document=snapshot.google_document, original=original
        )
        requests = preserve_in_place_native_list_text_styles(
            requests, original=original, transformed=transformed
        )
        return [
            item
            for item in requests
            if item.get("updateDocumentStyle", {}).get("tabId") not in preserved_tabs
        ]

    async def _apply_checkpoint_stage(self, checkpoint: InPlaceCheckpoint) -> InPlaceStageResult:
        if not paragraph_layout_requests_safe(checkpoint.requests, checkpoint.paragraph_layout):
            raise OutputConflictError
        allowed_header_requests = (
            tuple(
                build_custom_header_title_requests(
                    checkpoint.source_title,
                    profile=self._brand,
                    headers=checkpoint.custom_header_title_targets,
                )
            )
            if checkpoint.stage is InPlaceCheckpointStage.CONTENT
            else ()
        )
        if not custom_furniture_requests_safe(
            checkpoint.requests,
            checkpoint.custom_furniture,
            allowed_header_requests=allowed_header_requests,
        ):
            raise OutputConflictError
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
        findings = _with_tab_naming_decline(
            checkpoint.findings,
            declined_reason=checkpoint.tab_naming_declined_reason,
        )
        return DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=checkpoint.source_sha256,
            output=output,
            mode=request.mode,
            applied_patch_count=checkpoint.applied_patch_count,
            findings=findings,
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
            needs_review=_findings_need_review(findings),
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
            expected_parent_folder_id=snapshot.parent_folder_id,
        )
        if not unchanged:
            raise SourceChangedError

    async def _validate_applied_output(
        self,
        *,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        expected: DocumentIR,
        draft: OutputDraft,
        header_title: str,
    ) -> AppliedValidation:
        verification = await self._gateway.verify_output(
            draft=draft,
            source=snapshot,
            expected=expected,
            verify_brand=request.mode is not TransformMode.UNSLOP,
            header_title=header_title,
        )
        if not verification.content_preserved or not verification.structure_preserved:
            raise OutputVerificationError
        findings: list[TransformFinding] = []
        if not verification.brand_applied:
            findings.append(
                TransformFinding(
                    code=(
                        "custom_furniture_preserved"
                        if verification.supported_brand_applied
                        else "post_write_brand_mismatch"
                    ),
                    detail=(
                        "Supported body formatting was applied; existing custom headers, footers, and page geometry were preserved. Complete Sarj branding was not applied."
                        if verification.supported_brand_applied
                        else "Output did not contain the complete pinned Sarj brand treatment"
                    ),
                    severity=(
                        FindingSeverity.INFORMATIONAL
                        if verification.supported_brand_applied
                        else FindingSeverity.REVIEW
                    ),
                )
            )
        return AppliedValidation(
            protected_content_preserved=verification.content_preserved,
            structure_preserved=verification.structure_preserved,
            supported_brand_applied=verification.supported_brand_applied,
            findings=tuple(findings),
            revision_id=verification.revision_id or draft.revision_id,
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

    async def _create_output(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: SourceSnapshot,
        source_sha256: str,
        original: DocumentIR,
        transformed: DocumentIR,
        output_name: str | None = None,
    ) -> OutputDraft:
        output_name = output_name or _copy_output_name(request, original)
        imported_docx = None
        if snapshot.markdown is not None:
            imported_docx = render_markdown_docx(
                rewrite_markdown(snapshot.markdown, original, transformed),
                title=snapshot.title,
                header_title=output_name,
                logo_bytes=self._logo_bytes,
                branded=request.mode is not TransformMode.UNSLOP,
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
                parent_folder_id=request.copy_destination_folder_id or snapshot.parent_folder_id,
            )
        return await self._gateway.create_from_markdown(
            operation_id=operation_id,
            source=request.source,
            source_sha256=source_sha256,
            output_name=output_name,
            imported_docx=imported_docx,
        )

    async def _require_qa_destination_permission(
        self,
        request: DocumentTransformRequest,
    ) -> None:
        binding = _qa_destination_permission(request)
        if binding is None:
            return
        destination_folder_id = request.copy_destination_folder_id
        verifier = self._qa_destination_permission_verifier
        if destination_folder_id is None or verifier is None:
            raise DestinationAccessChangedError
        await verifier(destination_folder_id, binding)

    async def _require_qa_created_copy_permission(
        self,
        *,
        operation_id: uuid.UUID,
        source_sha256: str,
        request: DocumentTransformRequest,
        draft: OutputDraft,
    ) -> QaCopyPermissionEvidence | None:
        binding = _qa_destination_permission(request)
        if binding is None:
            return None
        destination_folder_id = request.copy_destination_folder_id
        verifier = self._qa_created_copy_verifier
        if destination_folder_id is None or verifier is None:
            raise DestinationAccessChangedError
        return await verifier(
            QaCreatedCopyPermissionRequest(
                output_file_id=draft.output.file_id,
                destination_folder_id=destination_folder_id,
                operation_id=operation_id,
                source_sha256=source_sha256,
                output_revision_id=draft.revision_id,
                binding=binding,
            )
        )

    async def _record_qa_quarantine(
        self,
        *,
        checkpoint: CopyCheckpoint,
        operation_id: uuid.UUID,
        source_sha256: str,
        request: DocumentTransformRequest,
        draft: OutputDraft,
    ) -> None:
        destination_folder_id = request.copy_destination_folder_id
        if destination_folder_id is None:  # pragma: no cover - schema invariant
            raise DestinationAccessChangedError
        quarantine = QaQuarantinedOutputEvidence(
            operation_id=operation_id,
            source_sha256=source_sha256,
            output_file_id=draft.output.file_id,
            destination_folder_id=destination_folder_id,
            output_revision_id=draft.revision_id,
            observed_at=datetime.now(UTC),
        )
        await self._save_copy_checkpoint(
            CopyCheckpoint.model_validate(
                checkpoint.model_dump(mode="python")
                | {
                    "stage": CopyCheckpointStage.QUARANTINED,
                    "output": draft.output,
                    "output_revision_id": draft.revision_id,
                    "result": None,
                    "qa_permission_evidence": None,
                    "qa_quarantine": quarantine,
                }
            )
        )


def _public_result_payload(result: DocumentTransformResult) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload.pop("qa_permission_evidence", None)
    return payload


def _qa_destination_permission(
    request: DocumentTransformRequest,
) -> QaDestinationPermissionBinding | None:
    return (
        request.qa_destination_permission
        if isinstance(request, QaDocumentTransformRequest)
        else None
    )


def _copy_output_name(request: DocumentTransformRequest, original: DocumentIR) -> str:
    if request.output_name is not None:
        return (
            request.output_name
            if request.mode is TransformMode.UNSLOP
            else polished_document_name(request.output_name)
        )
    title = (
        original.title
        if request.mode is TransformMode.UNSLOP
        else _strip_generated_copy_suffixes(suggested_document_title(original))
    )
    suffix = f" — {_OUTPUT_SUFFIX[request.mode]}"
    # COPY names share the request/checkpoint limit. Never truncate the source
    # metadata itself; reserve room for the review-copy suffix before persisting.
    base = title.strip()[: _MAX_OUTPUT_NAME_LENGTH - len(suffix)].rstrip()
    if request.mode is not TransformMode.UNSLOP:
        base = _strip_generated_copy_suffixes(base)
        base = polished_document_name(base)
    name = f"{base}{suffix}"
    return name if request.mode is TransformMode.UNSLOP else title_case_document_name(name)


def _copy_output_parent(request: DocumentTransformRequest, snapshot: SourceSnapshot) -> FileId:
    return request.copy_destination_folder_id or snapshot.parent_folder_id


def _strip_generated_copy_suffixes(title: str) -> str:
    title = title.strip()
    for _ in range(_MAX_GENERATED_NAME_SUFFIXES):
        suffix = next(
            (
                value
                for value in (" — Sarj formatted", " — Sarj polished")
                if title.casefold().endswith(value.casefold())
            ),
            None,
        )
        if suffix is None:
            break
        title = title[: -len(suffix)].rstrip()
    return title or "Untitled"


def _copy_font_outcome(
    snapshot: SourceSnapshot, original: DocumentIR, outcome: TransformOutcome
) -> TransformOutcome:
    if not original.copy_font_defended:
        return outcome
    preserved_ids = copy_preserved_font_ids(snapshot.google_document, original, outcome.document)
    return replace(
        outcome,
        document=apply_native_font_preservation(outcome.document, preserved_ids),
        findings=outcome.findings
        + tuple(
            TransformFinding(
                code="native_font_family_preserved",
                block_id=block_id,
                detail="Authored font family was preserved because its complete bold and weight semantics could not be safely changed.",
                severity=FindingSeverity.INFORMATIONAL,
            )
            for block_id in preserved_ids
            if block_id not in _copy_visual_ids(original)
        ),
    )


def _copy_visual_ids(document: DocumentIR) -> tuple[str, ...]:
    return tuple(block.id for block in document.blocks if block.preserved_visual_authority)


def _copy_builder_version(document: DocumentIR) -> str:
    if document.copy_font_defended:
        return f"{REQUEST_BUILDER_VERSION}-copy-font-v1"
    return _COPY_REQUEST_BUILDER_VERSION if _copy_visual_ids(document) else REQUEST_BUILDER_VERSION


def _new_copy_checkpoint(
    *,
    request: DocumentTransformRequest,
    snapshot: SourceSnapshot,
    original: DocumentIR,
    outcome: TransformOutcome,
    source_sha256: str,
    model_ids: list[ModelId],
    logo_sha256: str | None,
) -> CopyCheckpoint:
    replacements = {
        before.id: after.text
        for before, after in zip(original.blocks, outcome.document.blocks, strict=True)
        if before.text != after.text
    }
    authored_labels = (
        plan_authored_labels(original) if request.mode is not TransformMode.UNSLOP else ()
    )
    pagination = (
        plan_native_pagination(snapshot.google_document, outcome.document, emit_diagnostics=True)
        if request.mode is not TransformMode.UNSLOP
        else ()
    )
    output_name = _copy_output_name(request, original)
    plan_payload = _copy_plan_payload(
        output_name=output_name,
        replacements=replacements,
        authored_labels=authored_labels,
        pagination=pagination,
        findings=list(outcome.findings),
        applied_patch_count=outcome.applied_count,
        deterministic_patch_count=outcome.deterministic_count,
        model_patch_count=outcome.model_count,
        llm_invoked=outcome.llm_invoked,
        model_ids=model_ids,
        expected_semantic_sha256=outcome.document.semantic_sha256,
        logo_sha256=logo_sha256,
    )
    return CopyCheckpoint(
        authored_labels=authored_labels,
        pagination=pagination,
        source_title=snapshot.title,
        source_sha256=source_sha256,
        source_modified_time=snapshot.modified_time,
        source_revision_id=snapshot.revision_id,
        source_parent_folder_id=snapshot.parent_folder_id,
        output_parent_folder_id=request.copy_destination_folder_id or snapshot.parent_folder_id,
        qa_destination_permission=_qa_destination_permission(request),
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        pipeline_version=PIPELINE_VERSION,
        request_builder_version=_copy_builder_version(original),
        unslop_catalog_sha256=UNSLOP_CATALOG_SHA256,
        original_semantic_sha256=original.semantic_sha256,
        expected_semantic_sha256=outcome.document.semantic_sha256,
        plan_sha256=_json_sha256(plan_payload),
        manifest_sha256=_copy_manifest_sha256(
            snapshot,
            original,
            outcome.document,
            branded=request.mode is not TransformMode.UNSLOP,
            header_title=output_name,
        ),
        logo_sha256=logo_sha256,
        output_name=output_name,
        replacements=replacements,
        findings=list(outcome.findings),
        applied_patch_count=outcome.applied_count,
        deterministic_patch_count=outcome.deterministic_count,
        model_patch_count=outcome.model_count,
        llm_invoked=outcome.llm_invoked,
        model_ids=model_ids,
        stage=CopyCheckpointStage.PLANNED,
    )


def _validate_copy_checkpoint(
    checkpoint: CopyCheckpoint,
    *,
    request: DocumentTransformRequest,
    snapshot: SourceSnapshot,
    original: DocumentIR,
    source_sha256: str,
    current_logo_sha256: str | None,
) -> None:
    expected_contract = (
        snapshot.title,
        source_sha256,
        snapshot.modified_time,
        snapshot.revision_id,
        snapshot.parent_folder_id,
        request.copy_destination_folder_id or snapshot.parent_folder_id,
        _qa_destination_permission(request),
        CURRENT_BRAND_MANIFEST.profile_id,
        CURRENT_BRAND_MANIFEST.sha256,
        PIPELINE_VERSION,
        _copy_builder_version(original),
        UNSLOP_CATALOG_SHA256,
        original.semantic_sha256,
        _copy_output_name(request, original),
        current_logo_sha256,
    )
    checkpoint_contract = (
        checkpoint.source_title,
        checkpoint.source_sha256,
        checkpoint.source_modified_time,
        checkpoint.source_revision_id,
        checkpoint.source_parent_folder_id,
        checkpoint.output_parent_folder_id or checkpoint.source_parent_folder_id,
        checkpoint.qa_destination_permission,
        checkpoint.brand_profile_id,
        checkpoint.brand_profile_sha256,
        checkpoint.pipeline_version,
        checkpoint.request_builder_version,
        checkpoint.unslop_catalog_sha256,
        checkpoint.original_semantic_sha256,
        checkpoint.output_name,
        checkpoint.logo_sha256,
    )
    if checkpoint_contract != expected_contract:
        raise OutputConflictError
    if not set(_copy_visual_ids(original)).isdisjoint(checkpoint.replacements) or tuple(
        finding.block_id
        for finding in checkpoint.findings
        if finding.code == "native_visual_authority_preserved"
    ) != _copy_visual_ids(original):
        raise OutputConflictError
    if checkpoint.authored_labels != (
        plan_authored_labels(original) if request.mode is not TransformMode.UNSLOP else ()
    ):
        raise OutputConflictError
    transformed = apply_authored_labels(
        original.replace_blocks(checkpoint.replacements), checkpoint.authored_labels
    )
    if checkpoint.pagination != (
        plan_native_pagination(snapshot.google_document, transformed)
        if request.mode is not TransformMode.UNSLOP
        else ()
    ):
        raise OutputConflictError
    transformed = apply_native_pagination(transformed, checkpoint.pagination)
    if original.copy_font_defended:
        preserved_ids = copy_preserved_font_ids(snapshot.google_document, original, transformed)
        if tuple(
            finding.block_id
            for finding in checkpoint.findings
            if finding.code == "native_font_family_preserved"
        ) != tuple(
            block_id for block_id in preserved_ids if block_id not in _copy_visual_ids(original)
        ):
            raise OutputConflictError
        transformed = apply_native_font_preservation(transformed, preserved_ids)
    if transformed.semantic_sha256 != checkpoint.expected_semantic_sha256:
        raise OutputConflictError
    plan_payload = _copy_plan_payload(
        output_name=checkpoint.output_name,
        replacements=checkpoint.replacements,
        authored_labels=checkpoint.authored_labels,
        pagination=checkpoint.pagination,
        findings=checkpoint.findings,
        applied_patch_count=checkpoint.applied_patch_count,
        deterministic_patch_count=checkpoint.deterministic_patch_count,
        model_patch_count=checkpoint.model_patch_count,
        llm_invoked=checkpoint.llm_invoked,
        model_ids=checkpoint.model_ids,
        expected_semantic_sha256=checkpoint.expected_semantic_sha256,
        logo_sha256=checkpoint.logo_sha256,
    )
    if checkpoint.plan_sha256 != _json_sha256(plan_payload):
        raise OutputConflictError
    if checkpoint.manifest_sha256 != _copy_manifest_sha256(
        snapshot,
        original,
        transformed,
        branded=request.mode is not TransformMode.UNSLOP,
        header_title=checkpoint.output_name,
    ):
        raise OutputConflictError


def _copy_checkpoint_outcome(
    checkpoint: CopyCheckpoint,
    original: DocumentIR,
) -> TransformOutcome:
    document = apply_native_pagination(
        apply_authored_labels(
            original.replace_blocks(checkpoint.replacements), checkpoint.authored_labels
        ),
        checkpoint.pagination,
    )
    if original.copy_font_defended:
        preserved = {
            finding.block_id
            for finding in checkpoint.findings
            if finding.code == "native_font_family_preserved"
        } | set(_copy_visual_ids(original))
        document = apply_native_font_preservation(
            document, tuple(block.id for block in original.blocks if block.id in preserved)
        )
    return TransformOutcome(
        document=document,
        findings=tuple(checkpoint.findings),
        applied_count=checkpoint.applied_patch_count,
        deterministic_count=checkpoint.deterministic_patch_count,
        model_count=checkpoint.model_patch_count,
        llm_invoked=checkpoint.llm_invoked,
    )


def _copy_plan_payload(
    *,
    output_name: str,
    replacements: dict[str, str],
    authored_labels: tuple[AuthoredLabelRole, ...],
    pagination: tuple[NativePaginationRule, ...],
    findings: list[TransformFinding],
    applied_patch_count: int,
    deterministic_patch_count: int,
    model_patch_count: int,
    llm_invoked: bool,
    model_ids: list[ModelId],
    expected_semantic_sha256: str,
    logo_sha256: str | None,
) -> dict[str, Any]:
    return {
        "output_name": output_name,
        "replacements": replacements,
        "authored_labels": [role.model_dump(mode="json") for role in authored_labels],
        "pagination": [rule.model_dump(mode="json") for rule in pagination],
        "findings": [finding.model_dump(mode="json") for finding in findings],
        "applied_patch_count": applied_patch_count,
        "deterministic_patch_count": deterministic_patch_count,
        "model_patch_count": model_patch_count,
        "llm_invoked": llm_invoked,
        "model_ids": list(model_ids),
        "expected_semantic_sha256": expected_semantic_sha256,
        "logo_sha256": logo_sha256,
    }


def _copy_manifest_sha256(
    snapshot: SourceSnapshot,
    original: DocumentIR,
    transformed: DocumentIR,
    *,
    branded: bool,
    header_title: str,
) -> str:
    if snapshot.markdown is not None:
        rendered = rewrite_markdown(snapshot.markdown, original, transformed)
        manifest: object = asdict(
            markdown_import_manifest(
                rendered, title=snapshot.title, branded=branded, header_title=header_title
            )
        )
    else:
        manifest = {
            "expected_semantic_sha256": transformed.semantic_sha256,
            "source_format": transformed.source_format,
            "tab_ids": list(transformed.tab_ids),
            "branded": branded,
            "header_title": header_title if branded else None,
        }
        if transformed.copy_font_defended:
            if snapshot.google_document is None:
                raise OutputConflictError
            manifest = {
                **manifest,
                "copy_font_proofs": [
                    proof.model_dump(mode="json")
                    for proof in copy_font_proofs(snapshot.google_document, transformed)
                ],
            }
    return hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _json_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


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


def _matches_pre_header_checkpoint(
    snapshot: SourceSnapshot,
    inspection: InPlaceInspection,
    checkpoint: InPlaceCheckpoint,
) -> bool:
    if snapshot.revision_id is None:
        return False
    requested_tab_ids = _header_request_tab_ids(checkpoint.requests)
    if requested_tab_ids is None:
        return False
    current_headers = {str(header.tab_id): str(header.header_id) for header in inspection.headers}
    return (
        _to_ir(snapshot).semantic_sha256 == checkpoint.original_semantic_sha256
        and inspection.structural_sha256 == checkpoint.structural_sha256
        and inspection.protected_structure_sha256 == checkpoint.protected_structure_sha256
        and inspection.body_sha256 == checkpoint.body_sha256
        and inspection.header_safe_sha256 == checkpoint.header_safe_sha256
        and inspection.custom_furniture == checkpoint.custom_furniture
        and not inspection.unknown_header_tab_ids
        and tuple(str(tab_id) for tab_id in inspection.missing_header_tab_ids) == requested_tab_ids
        and current_headers == checkpoint.headers
        and {str(header.tab_id): str(header.header_id) for header in inspection.preserved_headers}
        == checkpoint.preserved_headers
        and inspection.marker_range == checkpoint.marker_range
    )


def _is_complex_structure_safe_noop(checkpoint: InPlaceCheckpoint) -> bool:
    return (
        checkpoint.stage is InPlaceCheckpointStage.COMPLETE
        and checkpoint.changed is False
        and not checkpoint.requests
        and not checkpoint.replacements
        and checkpoint.marker_name is None
        and checkpoint.marker_range is None
        and checkpoint.mutation_sha256 is None
        and checkpoint.table_font_plan is None
        and checkpoint.tab_naming is None
        and checkpoint.tab_naming_declined_reason is None
        and checkpoint.applied_patch_count == 0
        and checkpoint.deterministic_patch_count == 0
        and checkpoint.model_patch_count == 0
        and checkpoint.expected_semantic_sha256 == checkpoint.original_semantic_sha256
        and checkpoint.findings
        == _complex_structure_safe_noop_findings(
            visual_ids=checkpoint.visual_authority_block_ids,
            preserved_font_ids=checkpoint.preserved_font_block_ids,
            paragraph_layout=checkpoint.paragraph_layout,
        )
    )


def _claims_complex_structure_safe_noop(checkpoint: InPlaceCheckpoint) -> bool:
    return any(
        finding.code == "complex_native_structure_preserved" for finding in checkpoint.findings
    )


def _complex_structure_safe_noop_findings(
    *,
    visual_ids: tuple[str, ...],
    preserved_font_ids: tuple[str, ...],
    paragraph_layout: tuple[NativeParagraphLayout, ...],
) -> list[TransformFinding]:
    return [
        TransformFinding(
            code="complex_native_structure_preserved",
            detail=(
                "Complex rich text and protected native structure were preserved unchanged "
                "because a safe in-place transformation could not be proved."
            ),
            severity=FindingSeverity.REVIEW,
        ),
        *[
            TransformFinding(
                code="native_visual_authority_preserved",
                block_id=block_id,
                detail="Authored visual styling was preserved unchanged.",
                severity=FindingSeverity.INFORMATIONAL,
            )
            for block_id in visual_ids
        ],
        *[
            TransformFinding(
                code="native_font_family_preserved",
                block_id=block_id,
                detail="This paragraph's authored font family was preserved unchanged.",
                severity=FindingSeverity.INFORMATIONAL,
            )
            for block_id in preserved_font_ids
        ],
        *[
            TransformFinding(
                code="native_paragraph_layout_preserved",
                block_id=item.block_id,
                detail="This paragraph's authored layout was preserved unchanged.",
                severity=FindingSeverity.INFORMATIONAL,
            )
            for item in paragraph_layout
        ],
    ]


def _matches_complex_structure_safe_noop(
    snapshot: SourceSnapshot,
    inspection: InPlaceInspection,
    checkpoint: InPlaceCheckpoint,
) -> bool:
    if not _is_complex_structure_safe_noop(checkpoint) or snapshot.revision_id is None:
        return False
    document = _to_ir(snapshot)
    return (
        snapshot.title == checkpoint.source_title
        and snapshot.modified_time == checkpoint.source_modified_time
        and snapshot.revision_id == checkpoint.source_revision_id
        and snapshot.revision_id == checkpoint.current_revision_id
        and _source_sha256(snapshot, document) == checkpoint.source_sha256
        and document.semantic_sha256 == checkpoint.original_semantic_sha256
        and existing_native_pagination(snapshot.google_document, document) == checkpoint.pagination
        and inspection.structural_sha256 == checkpoint.structural_sha256
        and inspection.protected_structure_sha256 == checkpoint.protected_structure_sha256
        and inspection.body_sha256 == checkpoint.body_sha256
        and inspection.header_safe_sha256 == checkpoint.header_safe_sha256
        and inspection.custom_furniture == checkpoint.custom_furniture
        and inspection.header_identity == checkpoint.header_identity
        and not inspection.missing_header_tab_ids
        and not inspection.unknown_header_tab_ids
        and {str(header.tab_id): str(header.header_id) for header in inspection.headers}
        == checkpoint.headers
        and {str(header.tab_id): str(header.header_id) for header in inspection.preserved_headers}
        == checkpoint.preserved_headers
        and verify_paragraph_layout(
            snapshot.google_document, checkpoint.paragraph_layout, applied=False
        )
        and checkpoint.native_font_proof is not None
        and verify_native_font_proof(
            snapshot.google_document,
            checkpoint.native_font_proof,
            preserved_block_ids=_preserved_block_ids(checkpoint),
            font_policy=NativeFontVerificationPolicy(
                preserved_family_block_ids=checkpoint.preserved_font_block_ids,
            ),
        )
    )


def _header_request_tab_ids(requests: list[dict[str, Any]]) -> tuple[str, ...] | None:
    tab_ids: list[str] = []
    for request in requests:
        if set(request) != {"createHeader"}:
            return None
        creation = request.get("createHeader")
        if not isinstance(creation, dict) or creation.get("type") != "DEFAULT":
            return None
        location = creation.get("sectionBreakLocation")
        if not isinstance(location, dict) or location.get("index") != 0:
            return None
        tab_id = location.get("tabId")
        if not isinstance(tab_id, str) or not tab_id or tab_id in tab_ids:
            return None
        tab_ids.append(tab_id)
    return tuple(tab_ids) if tab_ids else None


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
    extra_requests: tuple[dict[str, Any], ...] = (),
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
            header_title=(
                _copy_output_name(request, original)
                if request.output_strategy is OutputStrategy.COPY
                else None
            ),
        )
    if request.output_strategy is OutputStrategy.COPY and transformed.copy_font_defended:
        requests = defend_copy_fonts(
            requests,
            document=snapshot.google_document,
            original=original,
            transformed=transformed,
            font_family=brand.font_family,
        )
    if (
        request.output_strategy is OutputStrategy.IN_PLACE
        and request.mode is not TransformMode.UNSLOP
    ):
        requests = defend_native_font_requests(
            requests, document=snapshot.google_document, original=original
        )
        requests = preserve_in_place_native_list_text_styles(
            requests, original=original, transformed=transformed
        )
    mutation_sha256(requests + list(extra_requests))


def _brand_warning_detail(code: str) -> str:
    if code == "logo_unavailable":
        return "Typography and spacing were applied, but the logo URL was not publicly readable"
    return "Existing header preserved for review"


def _findings_need_review(findings: list[TransformFinding]) -> bool:
    return any(finding.severity is FindingSeverity.REVIEW for finding in findings)


def _with_tab_naming_decline(
    findings: list[TransformFinding], *, declined_reason: TabNamingDeclinedReason | None
) -> list[TransformFinding]:
    if declined_reason is None or any(
        finding.code == "tab_naming_preserved" for finding in findings
    ):
        return findings
    return [
        *findings,
        TransformFinding(
            code="tab_naming_preserved",
            detail=(
                "Tab names and icons were preserved because this document's tab metadata "
                "could not be safely renamed."
            ),
            severity=FindingSeverity.INFORMATIONAL,
        ),
    ]


def _classify_verified_copy_planner_findings(
    findings: list[TransformFinding],
    *,
    planner_finding_count: int,
    protected_content_preserved: bool,
    structure_preserved: bool,
) -> list[TransformFinding]:
    planner_findings = findings[:planner_finding_count]
    if protected_content_preserved and structure_preserved:
        planner_findings = [
            finding.model_copy(update={"severity": FindingSeverity.INFORMATIONAL})
            if finding.code == "google_doc_text_target_unsupported"
            else finding
            for finding in planner_findings
        ]
    return planner_findings + findings[planner_finding_count:]


def _classify_supported_copy_brand_findings(
    findings: list[TransformFinding], *, supported_brand_applied: bool
) -> list[TransformFinding]:
    if not supported_brand_applied:
        return findings
    return [
        finding.model_copy(update={"severity": FindingSeverity.INFORMATIONAL})
        if finding.code.startswith("header_conflict:")
        else finding
        for finding in findings
    ]


def _preserved_block_ids(checkpoint: InPlaceCheckpoint) -> tuple[str, ...]:
    return tuple(
        finding.block_id or ""
        for finding in checkpoint.findings
        if finding.code == "native_rich_text_preserved"
    )
