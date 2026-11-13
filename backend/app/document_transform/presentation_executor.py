from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import TYPE_CHECKING, assert_never, override
import uuid

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.checkpoint_transitions import (
    begin_presentation,
    record_presentation,
    verify_presentation,
)
from app.document_transform.errors import (
    ExternalEffectUncertainError,
    InPlaceNotAllowedError,
    OutputConflictError,
    SourceChangedError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.executor import DocumentTransformCheckpointStore
from app.document_transform.models import PatchGenerator
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.presentation_gateway import (
    PresentationDraft,
    PresentationGateway,
    PresentationSourceSnapshot,
)
from app.document_transform.presentation_state import (
    AppliedPresentationMutation,
    CompletedPresentationMutation,
    pending_mutation_plan,
)
from app.document_transform.runner import DocumentTransformRunner
from app.document_transform.schemas import (
    CreatedPresentationCheckpoint,
    CreatingPresentationCheckpoint,
    DocumentOutput,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    FindingSeverity,
    OutputStrategy,
    PlannedPresentationCheckpoint,
    PresentationCheckpointPayload,
    PresentationCopyCheckpoint,
    PresentationCopyPlan,
    TransformFinding,
    TransformMode,
    ValidationSummary,
    VerifiedPresentationCheckpoint,
)
from app.document_transform.slides_brand import build_slides_mutation_plan
from app.document_transform.slides_ir import ParsedPresentation, parse_google_presentation
from app.document_transform.unslop import (
    UNSLOP_CATALOG_SHA256,
    UNSLOP_UPSTREAM_COMMIT,
    UNSLOP_UPSTREAM_VERSION,
)
from app.document_transform.versions import PRESENTATION_PIPELINE_VERSION
from app.drive.schemas import MIME_SLIDE
from app.identifiers import DocsRevisionId, ModelId, TaskId


if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.document_transform.planner import TransformOutcome
    from app.document_transform.slides_brand import SlideMutationPlan


PRESENTATION_REQUEST_BUILDER_VERSION = "google-slides-requests-v1"
_OUTPUT_SUFFIX = MappingProxyType(
    {
        TransformMode.FORMAT: "Sarj formatted",
        TransformMode.UNSLOP: "Unslopped",
        TransformMode.BOTH: "Sarj polished",
    }
)


class PresentationTransformExecutor(DocumentTransformRunner):
    def __init__(
        self,
        *,
        gateway: PresentationGateway,
        generator: PatchGenerator,
        logo_uri: str,
        checkpoint_store: DocumentTransformCheckpointStore,
        lease_token: uuid.UUID,
        checkpoint: DocumentTransformCheckpoint | None,
        checkpoint_generation: int,
    ) -> None:
        self._gateway = gateway
        self._generator = generator
        self._planner = TextTransformPlanner(
            generator=generator, allow_unslop_model=generator.supports_semantic_rewrite
        )
        self._logo_uri = logo_uri
        self._store = checkpoint_store
        self._lease_token = lease_token
        self._checkpoint = checkpoint
        self._generation = checkpoint_generation

    @override
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> DocumentTransformResult:
        if request.output_strategy is OutputStrategy.IN_PLACE:
            raise InPlaceNotAllowedError
        source = request.source
        if not isinstance(source, DriveFileSource):
            raise UnsupportedSourceError
        snapshot = await self._gateway.load_source(source)
        if snapshot.modified_time != source.expected_modified_time:
            raise SourceChangedError

        source_parsed = self._parse_native_source(snapshot)
        source_sha256 = _source_sha256(snapshot, source_parsed)
        checkpoint = await self._checkpoint_for_request(
            operation_id, request, snapshot, source_sha256
        )
        recovered = await self._recover_output(
            operation_id=operation_id,
            request=request,
            snapshot=snapshot,
            source_sha256=source_sha256,
        )
        match recovered:
            case DocumentTransformResult():
                match checkpoint:
                    case VerifiedPresentationCheckpoint():
                        if checkpoint.output.file_id != recovered.output.file_id:
                            raise OutputConflictError
                        return checkpoint.result
                    case (
                        PlannedPresentationCheckpoint()
                        | CreatingPresentationCheckpoint()
                        | CreatedPresentationCheckpoint()
                    ):
                        await self._record_result(operation_id, checkpoint, recovered)
                        return recovered
                    case _:
                        assert_never(checkpoint)
            case PresentationDraft() | None:
                pass
            case _:
                assert_never(recovered)

        planned = await self._plan_native_source(snapshot, source_parsed, request)
        if not await self._gateway.source_is_unchanged(snapshot):
            raise SourceChangedError
        checkpoint, draft = await self._draft_for_checkpoint(
            operation_id, snapshot, source_sha256, checkpoint, recovered
        )
        checkpoint = await self._record_output(operation_id, checkpoint, _output(draft))
        output_presentation = await self._gateway.get_presentation(draft.file_id)
        try:
            output_parsed = parse_google_presentation(
                output_presentation,
                title=draft.name,
            )
        except SourceTooLargeError, UnsupportedSourceError:
            if snapshot.mime_type == MIME_SLIDE:
                raise
            result = self._conversion_review_result(
                operation_id=operation_id,
                request=request,
                source_sha256=source_sha256,
                draft=draft,
                source_revision_id=snapshot.revision_id,
            )
            await self._record_result(operation_id, checkpoint, result)
            return result

        findings: list[TransformFinding] = []
        if source_parsed is not None:
            if (
                output_parsed.document.semantic_sha256 != source_parsed.document.semantic_sha256
                or output_parsed.protected_sha256 != source_parsed.protected_sha256
            ):
                raise OutputConflictError
        else:
            planned = await self._planner.transform(output_parsed.document, request)
            findings.append(
                TransformFinding(
                    code="pptx_conversion_review_required",
                    detail=(
                        "PowerPoint was imported as native Google Slides; review visual fidelity "
                        "before relying on the converted presentation"
                    ),
                )
            )
        if planned is None:  # pragma: no cover - both source branches assign it
            message = "Presentation plan was not created"
            raise RuntimeError(message)
        findings.extend(planned.findings)
        mutation = build_slides_mutation_plan(
            output_presentation,
            parsed=output_parsed,
            transformed=planned.document,
            mode=request.mode,
            logo_uri=self._logo_uri,
        )
        findings.extend(mutation.findings)
        result = await self._apply_and_verify_output(
            operation_id=operation_id,
            request=request,
            snapshot=snapshot,
            source_sha256=source_sha256,
            draft=draft,
            planned=planned,
            output_parsed=output_parsed,
            mutation=mutation,
            findings=findings,
        )
        await self._record_result(operation_id, checkpoint, result)
        return result

    async def _draft_for_checkpoint(
        self,
        operation_id: uuid.UUID,
        snapshot: PresentationSourceSnapshot,
        source_sha256: str,
        checkpoint: PresentationCopyCheckpoint,
        recovered: PresentationDraft | None,
    ) -> tuple[PresentationCopyCheckpoint, PresentationDraft]:
        if recovered is not None:
            return checkpoint, recovered
        match checkpoint:
            case PlannedPresentationCheckpoint():
                creating = begin_presentation(checkpoint)
            case _:
                raise ExternalEffectUncertainError
        await self._save(operation_id, creating)
        try:
            draft = await self._gateway.create_copy(
                operation_id=operation_id,
                source=snapshot,
                source_sha256=source_sha256,
                output_name=creating.output_name,
            )
        except Exception as error:
            raise ExternalEffectUncertainError from error
        return creating, draft

    async def _checkpoint_for_request(
        self,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: PresentationSourceSnapshot,
        source_sha256: str,
    ) -> PresentationCopyCheckpoint:
        plan = PresentationCopyPlan(
            request_sha256=hashlib.sha256(
                json.dumps(
                    request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
            source_sha256=source_sha256,
            output_name=request.output_name or f"{snapshot.title} — {_OUTPUT_SUFFIX[request.mode]}",
        )
        checkpoint = self._checkpoint
        if checkpoint is None:
            planned = PlannedPresentationCheckpoint.model_validate(plan.model_dump())
            await self._save(operation_id, planned)
            return planned
        if not isinstance(checkpoint, PresentationCopyPlan):
            raise OutputConflictError
        checkpoint = PresentationCheckpointPayload.model_validate(checkpoint.model_dump()).root
        if (
            checkpoint.request_sha256 != plan.request_sha256
            or checkpoint.source_sha256 != plan.source_sha256
            or checkpoint.output_name != plan.output_name
        ):
            raise OutputConflictError
        return checkpoint

    async def _record_output(
        self,
        operation_id: uuid.UUID,
        checkpoint: PresentationCopyCheckpoint,
        output: DocumentOutput,
    ) -> CreatedPresentationCheckpoint | VerifiedPresentationCheckpoint:
        match checkpoint:
            case PlannedPresentationCheckpoint():
                creating = begin_presentation(checkpoint)
                await self._save(operation_id, creating)
                return await self._record_output(operation_id, creating, output)
            case CreatingPresentationCheckpoint():
                created = record_presentation(checkpoint, output)
                await self._save(operation_id, created)
                return created
            case CreatedPresentationCheckpoint() | VerifiedPresentationCheckpoint():
                if checkpoint.output.file_id != output.file_id:
                    raise OutputConflictError
                return checkpoint
            case _:
                assert_never(checkpoint)

    async def _record_result(
        self,
        operation_id: uuid.UUID,
        checkpoint: PresentationCopyCheckpoint,
        result: DocumentTransformResult,
    ) -> None:
        created = await self._record_output(operation_id, checkpoint, result.output)
        match created:
            case VerifiedPresentationCheckpoint():
                if created.result.output.file_id != result.output.file_id:
                    raise OutputConflictError
                return
            case CreatedPresentationCheckpoint():
                pass
            case _:
                assert_never(created)
        # The Drive revision and navigation URL can change during mutation.
        created = CreatedPresentationCheckpoint.model_validate(
            {**created.model_dump(), "output": result.output.model_dump()}
        )
        await self._save(operation_id, verify_presentation(created, result))

    async def _save(self, operation_id: uuid.UUID, checkpoint: PresentationCopyCheckpoint) -> None:
        self._generation = await self._store.save_document_transform_checkpoint(
            TaskId(operation_id),
            lease_token=self._lease_token,
            expected_generation=self._generation,
            checkpoint=checkpoint,
        )
        self._checkpoint = checkpoint

    async def _recover_output(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: PresentationSourceSnapshot,
        source_sha256: str,
    ) -> PresentationDraft | DocumentTransformResult | None:
        recovered = await self._gateway.recover_output(
            operation_id,
            expected_parent_folder_id=snapshot.parent_folder_id,
        )
        if recovered is not None and recovered.source_sha256 != source_sha256:
            raise OutputConflictError
        if recovered is not None and pending_mutation_plan(recovered.mutation) is not None:
            recovered = await self._gateway.reconcile_pending(recovered)
        if recovered is not None and isinstance(recovered.mutation, CompletedPresentationMutation):
            return self._recovered_result(
                operation_id=operation_id,
                request=request,
                source_sha256=source_sha256,
                draft=recovered,
            )
        if recovered is not None and isinstance(recovered.mutation, AppliedPresentationMutation):
            await self._gateway.complete(draft=recovered)
            return self._recovered_result(
                operation_id=operation_id,
                request=request,
                source_sha256=source_sha256,
                draft=recovered,
            )

        return recovered

    async def _apply_and_verify_output(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        snapshot: PresentationSourceSnapshot,
        source_sha256: str,
        draft: PresentationDraft,
        planned: TransformOutcome,
        output_parsed: ParsedPresentation,
        mutation: SlideMutationPlan,
        findings: list[TransformFinding],
    ) -> DocumentTransformResult:
        applied = await self._gateway.apply_requests_once(
            draft=draft,
            mutation_sha256=mutation.mutation_sha256,
            requests=mutation.requests,
            required_revision_id=draft.revision_id,
            expected_semantic_sha256=planned.document.semantic_sha256,
            expected_protected_structure_sha256=output_parsed.protected_sha256,
            expected_brand_sha256=mutation.expected_brand_sha256,
        )
        verification = await self._gateway.verify_output(
            draft=applied,
            title=draft.name,
            expected_semantic_sha256=planned.document.semantic_sha256,
            expected_protected_structure_sha256=output_parsed.protected_sha256,
            expected_brand_sha256=mutation.expected_brand_sha256,
        )
        result_findings = [*findings]
        if not verification.content_preserved:
            result_findings.append(
                TransformFinding(
                    code="post_write_content_mismatch",
                    detail="Output text did not match the validated presentation plan",
                )
            )
        if not verification.structure_preserved:
            result_findings.append(
                TransformFinding(
                    code="post_write_structure_mismatch",
                    detail="Output slide topology or protected content changed unexpectedly",
                )
            )
        if not verification.brand_applied:
            result_findings.append(
                TransformFinding(
                    code="post_write_brand_mismatch",
                    detail="Output did not contain the complete pinned Sarj slide treatment",
                )
            )
        result = DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=source_sha256,
            output=_output(applied),
            mode=request.mode,
            applied_patch_count=planned.applied_count,
            findings=result_findings,
            validation=ValidationSummary(
                source_unchanged=True,
                protected_content_preserved=verification.content_preserved,
                structure_preserved=verification.structure_preserved,
                unsafe_patch_count=_review_finding_count(planned.findings),
            ),
            model_ids=[ModelId(model) for model in self._generator.model_ids]
            if planned.llm_invoked
            else [],
            tool_versions=_tool_versions(),
            pipeline_version=PRESENTATION_PIPELINE_VERSION,
            needs_review=bool(_review_finding_count(result_findings)),
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            deterministic_patch_count=planned.deterministic_count,
            model_patch_count=planned.model_count,
            llm_invoked=planned.llm_invoked,
            output_strategy=OutputStrategy.COPY,
            changed=bool(mutation.requests),
            source_revision_id=(
                DocsRevisionId(snapshot.revision_id) if snapshot.revision_id is not None else None
            ),
            output_revision_id=DocsRevisionId(applied.revision_id),
            artifact_kind="presentation",
            access_scope="inherited",
        )
        if (
            verification.content_preserved
            and verification.structure_preserved
            and verification.brand_applied
        ):
            await self._gateway.complete(draft=applied)
        return result

    async def _plan_native_source(
        self,
        snapshot: PresentationSourceSnapshot,
        source_parsed: ParsedPresentation | None,
        request: DocumentTransformRequest,
    ) -> TransformOutcome | None:
        planned = (
            await self._planner.transform(source_parsed.document, request)
            if source_parsed is not None
            else None
        )
        if source_parsed is not None:
            if planned is None:  # pragma: no cover - mirrors source parser branch
                message = "Native presentation plan was not created"
                raise RuntimeError(message)
            if snapshot.presentation is None:  # pragma: no cover - guarded by parser result
                raise UnsupportedSourceError
            build_slides_mutation_plan(
                snapshot.presentation,
                parsed=source_parsed,
                transformed=planned.document,
                mode=request.mode,
                logo_uri=self._logo_uri,
            )
        return planned

    @staticmethod
    def _parse_native_source(
        snapshot: PresentationSourceSnapshot,
    ) -> ParsedPresentation | None:
        if snapshot.presentation is None:
            return None
        if snapshot.presentation.get("revisionId") != snapshot.revision_id:
            raise SourceChangedError
        return parse_google_presentation(snapshot.presentation, title=snapshot.title)

    @staticmethod
    def _conversion_review_result(
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        source_sha256: str,
        draft: PresentationDraft,
        source_revision_id: str | None,
    ) -> DocumentTransformResult:
        finding = TransformFinding(
            code="pptx_conversion_not_safely_editable",
            detail=(
                "PowerPoint was imported, but its native slide structure could not be edited "
                "within the safety limits"
            ),
        )
        return DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=source_sha256,
            output=_output(draft),
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
            tool_versions=_tool_versions(),
            pipeline_version=PRESENTATION_PIPELINE_VERSION,
            needs_review=True,
            changed=False,
            source_revision_id=(
                DocsRevisionId(source_revision_id) if source_revision_id is not None else None
            ),
            output_revision_id=DocsRevisionId(draft.revision_id),
            artifact_kind="presentation",
            access_scope="inherited",
        )

    @staticmethod
    def _recovered_result(
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
        source_sha256: str,
        draft: PresentationDraft,
    ) -> DocumentTransformResult:
        return DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=source_sha256,
            output=_output(draft),
            mode=request.mode,
            applied_patch_count=0,
            findings=[
                TransformFinding(
                    code="recovered_completed_output",
                    detail=(
                        "The completed sibling was recovered after task interruption; review it "
                        "because detailed patch telemetry was not persisted"
                    ),
                )
            ],
            validation=ValidationSummary(
                source_unchanged=True,
                protected_content_preserved=False,
                structure_preserved=False,
                unsafe_patch_count=1,
            ),
            model_ids=[],
            tool_versions=_tool_versions(),
            pipeline_version=PRESENTATION_PIPELINE_VERSION,
            needs_review=True,
            changed=True,
            output_revision_id=DocsRevisionId(draft.revision_id),
            artifact_kind="presentation",
            access_scope="inherited",
        )


def _review_finding_count(findings: Iterable[TransformFinding]) -> int:
    return sum(finding.severity is FindingSeverity.REVIEW for finding in findings)


def _source_sha256(
    snapshot: PresentationSourceSnapshot,
    parsed: ParsedPresentation | None,
) -> str:
    payload = {
        "can_copy": snapshot.can_copy,
        "can_download": snapshot.can_download,
        "drive_version": snapshot.drive_version,
        "ir": parsed.document.text_sha256 if parsed is not None else None,
        "protected_structure": parsed.protected_sha256 if parsed is not None else None,
        "md5_checksum": snapshot.md5_checksum,
        "media_type": snapshot.mime_type,
        "modified_time": snapshot.modified_time,
        "revision_id": snapshot.revision_id,
        "size": snapshot.size,
        "source_name": snapshot.source_name,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _output(draft: PresentationDraft) -> DocumentOutput:
    return DocumentOutput(
        file_id=draft.file_id,
        name=draft.name,
        parent_folder_id=draft.parent_folder_id,
        web_url=draft.web_url,
        media_type=MIME_SLIDE,
    )


def _tool_versions() -> dict[str, str]:
    return {
        "request_builder": PRESENTATION_REQUEST_BUILDER_VERSION,
        "unslop_catalog_sha256": UNSLOP_CATALOG_SHA256,
        "unslop_commit": UNSLOP_UPSTREAM_COMMIT,
        "unslop_version": UNSLOP_UPSTREAM_VERSION,
    }
