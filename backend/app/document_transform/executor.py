from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from types import MappingProxyType
from typing import Any, NamedTuple, Protocol
import uuid

from loguru import logger

from app.document_transform.brand import (
    SarjBrandProfile,
    build_copy_mutations,
    build_copy_text_mutations,
    mutation_sha256,
)
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.docx_renderer import markdown_import_manifest, render_markdown_docx
from app.document_transform.errors import (
    DocumentGatewayError,
    InPlaceNeedsReviewError,
    InPlaceNotAllowedError,
    OutputConflictError,
    OutputVerificationError,
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
from app.document_transform.header_provenance import HeaderIdentityFinalization
from app.document_transform.ir import (
    MAX_DOC_TABS,
    DocumentIR,
    parse_google_document,
    parse_markdown,
    rewrite_markdown,
)
from app.document_transform.models import PatchGenerator
from app.document_transform.naming import suggested_document_title
from app.document_transform.native_footer_flow import footer_coverage_complete
from app.document_transform.planner import TextTransformPlanner, TransformOutcome
from app.document_transform.schemas import (
    CopyCheckpoint,
    CopyCheckpointStage,
    DocumentOutput,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    InPlaceCheckpoint,
    InPlaceCheckpointStage,
    OutputStrategy,
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
from app.identifiers import DocsRevisionId, DocsSegmentId, FileId, ModelId, TaskId


PIPELINE_VERSION = "document-transform-v3"
REQUEST_BUILDER_VERSION = "google-docs-requests-v10"
_MAX_OUTPUT_NAME_LENGTH = 255
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
        blocks=tuple(
            replace(block, text=transformed.text)
            for block, transformed in zip(mapped.blocks, expected.blocks, strict=True)
        ),
    )
    return StructureInputs(replace(snapshot, google_document=projected), mapped)


@dataclass(frozen=True, slots=True)
class AppliedValidation:
    protected_content_preserved: bool
    structure_preserved: bool
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


class DocumentTransformExecutor:
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
        source_sha256 = _source_sha256(snapshot, original)
        recovered = await self._gateway.recover_output(operation_id)
        checkpoint = self._copy_checkpoint_or_none()
        if checkpoint is None:
            if recovered is not None:
                # Legacy/incomplete outputs without a durable plan cannot be
                # re-attested safely and must never trigger a fresh model plan.
                raise OutputConflictError
            outcome = await self._planner.transform(original, request)
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
        if checkpoint.stage is CopyCheckpointStage.VERIFIED:
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
            )
            if checkpoint.managed_headers is None:
                # Persist positive ownership before body/header population.
                # Never infer ownership from an already-mutated recovered copy.
                checkpoint = await self._save_copy_checkpoint(
                    checkpoint.model_copy(
                        update={
                            "managed_headers": {}
                            if (draft.applied_mutation_sha256 or draft.pending_mutation_sha256)
                            else {
                                str(header.tab_id): str(header.header_id)
                                for header in prepared_draft.headers
                            }
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
                header_title=suggested_document_title(original),
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
            transform_findings=outcome.findings,
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
        findings.extend(applied_validation.findings)
        if checkpoint.native_footer is not None and checkpoint.native_footer.coverage is not None:
            findings.extend(
                TransformFinding(
                    code=f"native_footer_{reason}",
                    detail={
                        "custom_footer": "Existing custom footers were preserved on some tabs.",
                        "pageless": "Wiki footers were not added to pageless tabs.",
                        "unsupported_layout": "Wiki footers were not added where tab layout could not be safely verified.",
                    }[reason],
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
                )
            )
        validation = ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=applied_validation.protected_content_preserved,
            structure_preserved=applied_validation.structure_preserved,
            unsafe_patch_count=len(outcome.findings),
        )
        result = DocumentTransformResult(
            header_identity=(
                await self._gateway.capture_header_identity(
                    draft=applied,
                    operation_id=operation_id,
                    managed_headers=checkpoint.managed_headers or {},
                    title=suggested_document_title(original),
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
            needs_review=bool(findings),
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            deterministic_patch_count=outcome.deterministic_count,
            model_patch_count=outcome.model_count,
            llm_invoked=outcome.llm_invoked,
            output_revision_id=applied.revision_id,
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
    ) -> OutputDraft:
        if checkpoint.tab_naming is None:
            tab_naming = await self._gateway.plan_tab_naming(
                document_id=draft.document_id,
                title=checkpoint.source_title,
                operation_id=operation_id,
                required_revision_id=draft.revision_id,
            )
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
            checkpoint.stage is not CopyCheckpointStage.VERIFIED
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
        )
        revision_id = verification.revision_id or draft.revision_id
        if (
            not verification.content_preserved
            or not verification.structure_preserved
            or revision_id != checkpoint.output_revision_id
        ):
            raise OutputVerificationError
        serialized_result = result.model_dump(mode="json")
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
        )
        final_revision = final_verification.revision_id or draft.revision_id
        if (
            not final_verification.content_preserved
            or not final_verification.structure_preserved
            or final_revision != checkpoint.output_revision_id
        ):
            raise OutputVerificationError
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
                REQUEST_BUILDER_VERSION,
                UNSLOP_CATALOG_SHA256,
            )
            if checkpoint_contract != current_contract:
                raise OutputConflictError
            if checkpoint.stage is InPlaceCheckpointStage.COMPLETE:
                if snapshot.revision_id != checkpoint.current_revision_id:
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
        inspection = await self._gateway.inspect_in_place(snapshot=snapshot, title=snapshot.title)
        if checkpoint is not None and checkpoint.header_identity != inspection.header_identity:
            raise OutputConflictError
        if checkpoint is None:
            checkpoint = await self._plan_initial_in_place(
                operation_id=operation_id,
                request=request,
                snapshot=snapshot,
                inspection=inspection,
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
        before_content = _to_ir(snapshot)
        expected_content = before_content.replace_blocks(checkpoint.replacements)
        if expected_content.semantic_sha256 != checkpoint.expected_semantic_sha256:
            raise InPlaceNeedsReviewError
        if checkpoint.tab_naming is not None:
            if snapshot.google_document is None or snapshot.revision_id is None:
                raise InPlaceNeedsReviewError
            stage_result = InPlaceStageResult(snapshot.revision_id, snapshot.google_document)
        else:
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
        if checkpoint.tab_naming is None:
            tab_naming = await self._gateway.plan_tab_naming(
                document_id=checkpoint.document_id,
                title=checkpoint.source_title,
                operation_id=operation_id,
                required_revision_id=checkpoint.current_revision_id,
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
        outcome = await self._planner.transform(original, request)
        _preflight_native_mutation_budget(
            request=request,
            snapshot=snapshot,
            original=original,
            transformed=outcome.document,
            brand=self._brand,
        )
        if outcome.findings:
            logger.info(
                "document in-place preflight checks={checks}",
                checks={"in_place_planner_findings": True},
            )
            raise InPlaceNeedsReviewError
        branded = request.mode is not TransformMode.UNSLOP
        if branded and (inspection.unknown_header_tab_ids or not inspection.logo_uri):
            logger.info(
                "document in-place preflight checks={checks}",
                checks={
                    "in_place_unknown_header": bool(inspection.unknown_header_tab_ids),
                    "in_place_missing_logo": not bool(inspection.logo_uri),
                },
            )
            raise InPlaceNeedsReviewError
        replacements = {
            block.id: transformed.text
            for block, transformed in zip(original.blocks, outcome.document.blocks, strict=True)
            if block.text != transformed.text
        }
        model_ids = (
            [ModelId(value) for value in self._generator.model_ids] if outcome.llm_invoked else []
        )
        if branded and inspection.fully_branded and not replacements:
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
                original=original,
                transformed=outcome.document,
                inspection=inspection,
            )
            stage = InPlaceCheckpointStage.CONTENT
        changed = bool(requests)
        if requests and inspection.marker_range is None:
            logger.info(
                "document in-place preflight checks={checks}",
                checks={"in_place_missing_marker": True},
            )
            raise InPlaceNeedsReviewError
        checkpoint = InPlaceCheckpoint(
            header_identity=inspection.header_identity,
            document_id=source.file_id,
            source_title=snapshot.title,
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
            preserved_headers={
                str(header.tab_id): str(header.header_id) for header in inspection.preserved_headers
            },
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
        transform_findings: tuple[TransformFinding, ...],
    ) -> AppliedValidation:
        verification = await self._gateway.verify_output(
            draft=draft,
            source=snapshot,
            expected=expected,
            verify_brand=request.mode is not TransformMode.UNSLOP,
        )
        if not verification.content_preserved or not verification.structure_preserved:
            raise OutputVerificationError
        findings: list[TransformFinding] = []
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
            )
        return await self._gateway.create_from_markdown(
            operation_id=operation_id,
            source=request.source,
            source_sha256=source_sha256,
            output_name=output_name,
            imported_docx=imported_docx,
        )


def _copy_output_name(request: DocumentTransformRequest, original: DocumentIR) -> str:
    if request.output_name is not None:
        return request.output_name
    title = (
        original.title
        if request.mode is TransformMode.UNSLOP
        else suggested_document_title(original)
    )
    suffix = f" — {_OUTPUT_SUFFIX[request.mode]}"
    # COPY names share the request/checkpoint limit. Never truncate the source
    # metadata itself; reserve room for the review-copy suffix before persisting.
    return f"{title.strip()[: _MAX_OUTPUT_NAME_LENGTH - len(suffix)].rstrip()}{suffix}"


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
    output_name = _copy_output_name(request, original)
    plan_payload = _copy_plan_payload(
        output_name=output_name,
        replacements=replacements,
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
        source_title=snapshot.title,
        source_sha256=source_sha256,
        source_modified_time=snapshot.modified_time,
        source_revision_id=snapshot.revision_id,
        source_parent_folder_id=snapshot.parent_folder_id,
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        pipeline_version=PIPELINE_VERSION,
        request_builder_version=REQUEST_BUILDER_VERSION,
        unslop_catalog_sha256=UNSLOP_CATALOG_SHA256,
        original_semantic_sha256=original.semantic_sha256,
        expected_semantic_sha256=outcome.document.semantic_sha256,
        plan_sha256=_json_sha256(plan_payload),
        manifest_sha256=_copy_manifest_sha256(
            snapshot,
            original,
            outcome.document,
            branded=request.mode is not TransformMode.UNSLOP,
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
        CURRENT_BRAND_MANIFEST.profile_id,
        CURRENT_BRAND_MANIFEST.sha256,
        PIPELINE_VERSION,
        REQUEST_BUILDER_VERSION,
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
    transformed = original.replace_blocks(checkpoint.replacements)
    if transformed.semantic_sha256 != checkpoint.expected_semantic_sha256:
        raise OutputConflictError
    plan_payload = _copy_plan_payload(
        output_name=checkpoint.output_name,
        replacements=checkpoint.replacements,
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
    ):
        raise OutputConflictError


def _copy_checkpoint_outcome(
    checkpoint: CopyCheckpoint,
    original: DocumentIR,
) -> TransformOutcome:
    return TransformOutcome(
        document=original.replace_blocks(checkpoint.replacements),
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
) -> str:
    if snapshot.markdown is not None:
        rendered = rewrite_markdown(snapshot.markdown, original, transformed)
        manifest: object = asdict(
            markdown_import_manifest(rendered, title=snapshot.title, branded=branded)
        )
    else:
        manifest = {
            "expected_semantic_sha256": transformed.semantic_sha256,
            "source_format": transformed.source_format,
            "tab_ids": list(transformed.tab_ids),
            "branded": branded,
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
        and not inspection.unknown_header_tab_ids
        and tuple(str(tab_id) for tab_id in inspection.missing_header_tab_ids) == requested_tab_ids
        and current_headers == checkpoint.headers
        and {str(header.tab_id): str(header.header_id) for header in inspection.preserved_headers}
        == checkpoint.preserved_headers
        and inspection.marker_range == checkpoint.marker_range
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
                suggested_document_title(original)
                if request.output_strategy is OutputStrategy.COPY
                else None
            ),
        )
    mutation_sha256(requests)


def _brand_warning_detail(code: str) -> str:
    if code == "logo_unavailable":
        return "Typography and spacing were applied, but the logo URL was not publicly readable"
    return "Existing header preserved for review"
