from __future__ import annotations

from copy import deepcopy
from typing import Any, Never, TypedDict, Unpack, override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import (
    DocumentTransformError,
    ExternalEffectUncertainError,
    InPlaceNotAllowedError,
    OutputConflictError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.google_presentation_gateway import PPTX_MIME_TYPE
from app.document_transform.ir import DocumentIR
from app.document_transform.models import PatchGenerator
from app.document_transform.presentation_executor import PresentationTransformExecutor
from app.document_transform.presentation_gateway import (
    DriveFileVersion,
    NativePresentationSource,
    PptxPresentationSource,
    PresentationDraft,
    PresentationRevisionId,
    PresentationSourceSnapshot,
    PresentationVerification,
)
from app.document_transform.presentation_state import (
    AppliedPresentationMutation,
    PresentationMutationPlan,
    RecordedPresentationMutation,
)
from app.document_transform.schemas import (
    CreatingPresentationCheckpoint,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DriveFileSource,
    FindingSeverity,
    OutputStrategy,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.slides_ir import MAX_PRESENTATION_SLIDES, ParsedPresentation
from app.drive.schemas import MIME_SLIDE
from app.identifiers import FileId, TaskId
from tests.json_values import json_object_at


class _SlidesPlanArguments(TypedDict):
    parsed: ParsedPresentation
    transformed: DocumentIR
    mode: TransformMode
    logo_uri: str


class PresentationCheckpointStore:
    def __init__(self) -> None:
        self.checkpoints: list[DocumentTransformCheckpoint] = []
        self.resources: list[tuple[TaskId, uuid.UUID, FileId]] = []

    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int:
        assert expected_generation == len(self.checkpoints)
        self.checkpoints.append(checkpoint)
        return len(self.checkpoints)

    async def record_resource(
        self, task_id: TaskId, *, lease_token: uuid.UUID, resource_id: FileId
    ) -> None:
        self.resources.append((task_id, lease_token, resource_id))


class NoopPatchGenerator(PatchGenerator):
    def __init__(self) -> None:
        self.calls = 0

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("gemini-test-001",)

    @override
    async def generate(
        self,
        document: object,
        *,
        phase: TransformPhase,
    ) -> list[TextPatch]:
        del document, phase
        self.calls += 1
        return []


class PresentationGatewaySpy:
    def __init__(self, *, mime_type: str) -> None:
        source_presentation = _presentation("source", "source-revision")
        self.snapshot: PresentationSourceSnapshot
        if mime_type == PPTX_MIME_TYPE:
            self.snapshot = PptxPresentationSource(
                file_id=FileId("source"),
                title="Review",
                source_name="Review.pptx",
                parent_folder_id=FileId("folder"),
                modified_time="2026-09-04T00:00:00Z",
                drive_version=DriveFileVersion("7"),
                size=1024,
                md5_checksum="a" * 32,
                can_download=True,
                can_copy=False,
            )
        elif mime_type == MIME_SLIDE:
            self.snapshot = NativePresentationSource(
                file_id=FileId("source"),
                title="Review",
                source_name="Review",
                parent_folder_id=FileId("folder"),
                modified_time="2026-09-04T00:00:00Z",
                revision_id=PresentationRevisionId("source-revision"),
                drive_version=DriveFileVersion("7"),
                size=None,
                md5_checksum=None,
                can_download=False,
                can_copy=True,
                presentation=source_presentation,
            )
        else:
            message = "Unsupported presentation fixture kind"
            raise ValueError(message)
        self.output_presentation = _presentation("output", "output-revision")
        self.created = 0
        self.completed = 0
        self.requests: list[dict[str, Any]] = []
        self.required_revision_id: PresentationRevisionId | None = None
        self.recovered: PresentationDraft | None = None
        self.verification_content_preserved = True
        self.verification_structure_preserved = True
        self.verification_brand_applied = True

    async def load_source(self, source: DriveFileSource) -> PresentationSourceSnapshot:
        assert source.file_id == "source"
        return self.snapshot

    async def source_is_unchanged(self, snapshot: PresentationSourceSnapshot) -> bool:
        return snapshot == self.snapshot

    async def recover_output(
        self,
        operation_id: uuid.UUID,
        *,
        expected_parent_folder_id: FileId,
    ) -> PresentationDraft | None:
        del operation_id
        assert expected_parent_folder_id == "folder"
        return self.recovered

    async def create_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: PresentationSourceSnapshot,
        source_sha256: str,
        output_name: str,
    ) -> PresentationDraft:
        del operation_id
        assert source == self.snapshot
        self.created += 1
        return PresentationDraft(
            file_id=FileId("output"),
            name=output_name,
            parent_folder_id=FileId("folder"),
            revision_id=PresentationRevisionId("output-revision"),
            source_sha256=source_sha256,
            web_url="https://docs.google.com/presentation/d/output/edit",
        )

    async def reconcile_pending(self, draft: PresentationDraft) -> PresentationDraft:
        return draft

    async def get_presentation(self, file_id: FileId) -> dict[str, Any]:
        assert file_id == "output"
        return deepcopy(self.output_presentation)

    async def apply_requests_once(
        self,
        *,
        draft: PresentationDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: PresentationRevisionId,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None = None,
    ) -> PresentationDraft:
        self.request_invariants = (
            expected_semantic_sha256,
            expected_protected_structure_sha256,
            expected_brand_sha256,
        )
        self.requests = requests
        self.required_revision_id = required_revision_id
        return PresentationDraft(
            file_id=draft.file_id,
            name=draft.name,
            parent_folder_id=draft.parent_folder_id,
            revision_id=PresentationRevisionId("output-revision-2"),
            source_sha256=draft.source_sha256,
            web_url=draft.web_url,
            mutation=RecordedPresentationMutation(mutation_sha256=mutation_sha256),
        )

    async def verify_output(
        self,
        *,
        draft: PresentationDraft,
        title: str,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None = None,
    ) -> PresentationVerification:
        return PresentationVerification(
            content_preserved=self.verification_content_preserved,
            structure_preserved=self.verification_structure_preserved,
            brand_applied=self.verification_brand_applied,
            semantic_sha256=expected_semantic_sha256,
            protected_structure_sha256=expected_protected_structure_sha256,
            brand_sha256=expected_brand_sha256,
        )

    async def complete(self, *, draft: PresentationDraft) -> None:
        assert draft.file_id == "output"
        self.completed += 1


@pytest.fixture
def presentation_gateway_factory() -> type[PresentationGatewaySpy]:
    return PresentationGatewaySpy


class QuantityPatchGenerator(PatchGenerator):
    supports_semantic_rewrite = True

    def __init__(self, *, change_quantity: bool) -> None:
        self.calls = 0
        self._change_quantity = change_quantity

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("gemini-test-001",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        assert phase is TransformPhase.UNSLOP
        self.calls += 1
        block = document.blocks[0]
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=block.text.replace("15", "16") if self._change_quantity else block.text,
            )
        ]


@pytest.mark.parametrize(
    ("verbose", "change_quantity", "expected_findings", "expected_unsafe_count"),
    [
        pytest.param(
            True,
            False,
            {"concision_target_unmet": FindingSeverity.INFORMATIONAL},
            0,
            id="info-only",
        ),
        pytest.param(
            False,
            True,
            {"protected_content_changed": FindingSeverity.REVIEW},
            1,
            id="review-only",
        ),
        pytest.param(
            True,
            True,
            {
                "protected_content_changed": FindingSeverity.REVIEW,
                "concision_target_unmet": FindingSeverity.INFORMATIONAL,
            },
            1,
            id="mixed",
        ),
        pytest.param(False, False, {}, 0, id="no-findings"),
    ],
)
async def test_presentation_result_distinguishes_review_rejections_from_information(
    verbose: bool,
    change_quantity: bool,
    expected_findings: dict[str, FindingSeverity],
    expected_unsafe_count: int,
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    text = "The sensor records exactly 15 samples each night."
    if verbose:
        text += " The report presents the details of the account in extended prose." * 4
    assert isinstance(gateway.snapshot, NativePresentationSource)
    for presentation in (gateway.snapshot.presentation, gateway.output_presentation):
        run = json_object_at(
            presentation, "slides", 0, "pageElements", 0, "shape", "text", "textElements", 1
        )
        json_object_at(run, "textRun")["content"] = text + "\n"
        run["endIndex"] = len(text) + 1
    source_before = deepcopy(gateway.snapshot)
    generator = QuantityPatchGenerator(change_quantity=change_quantity)
    executor = _executor(
        gateway=gateway,
        generator=generator,
    )

    result = await executor.execute(
        operation_id=uuid.uuid7(),
        request=_request(output_strategy=OutputStrategy.COPY),
    )

    assert {finding.code: finding.severity for finding in result.findings} == expected_findings
    assert result.validation.unsafe_patch_count == expected_unsafe_count
    assert result.needs_review is bool(expected_unsafe_count)
    assert result.validation.source_unchanged
    assert result.validation.protected_content_preserved
    assert result.validation.structure_preserved
    assert result.model_patch_count == 0
    assert result.llm_invoked
    assert generator.calls == 1
    assert gateway.snapshot == source_before
    assert gateway.created == gateway.completed == 1
    assert gateway.required_revision_id == "output-revision"
    assert not any(
        "deleteText" in request or "insertText" in request for request in gateway.requests
    )


@pytest.mark.parametrize("mime_type", [MIME_SLIDE, PPTX_MIME_TYPE])
async def test_presentation_copy_is_revision_fenced_branded_and_keeps_source(
    mime_type: str,
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=mime_type)
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
    )

    result = await executor.execute(
        operation_id=uuid.uuid7(),
        request=_request(output_strategy=OutputStrategy.COPY),
    )

    assert gateway.created == 1
    assert gateway.completed == 1
    assert gateway.required_revision_id == "output-revision"
    assert any("updateTextStyle" in request for request in gateway.requests)
    assert any("createImage" in request for request in gateway.requests)
    assert result.output.file_id == "output"
    assert result.output.media_type == MIME_SLIDE
    assert result.artifact_kind == "presentation"
    assert result.access_scope == "inherited"
    assert result.output_strategy is OutputStrategy.COPY
    assert result.source_revision_id == ("source-revision" if mime_type == MIME_SLIDE else None)
    assert result.output_revision_id == "output-revision-2"
    assert result.deterministic_patch_count == 0
    assert result.llm_invoked is False
    assert ("pptx_conversion_review_required" in {item.code for item in result.findings}) is (
        mime_type == PPTX_MIME_TYPE
    )
    assert result.needs_review is (mime_type == PPTX_MIME_TYPE)


@pytest.mark.parametrize(
    "changed_field",
    [
        pytest.param("text", id="changed-text"),
        pytest.param("object_id", id="changed-protected-object-id"),
    ],
)
async def test_native_copy_fingerprint_mismatch_rejects_before_mutation(
    changed_field: str,
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    output_body = gateway.output_presentation["slides"][0]["pageElements"][0]
    if changed_field == "text":
        text_run = output_body["shape"]["text"]["textElements"][1]
        changed_text = "This copy has different text.\n"
        json_object_at(text_run, "textRun")["content"] = changed_text
        text_run["endIndex"] = len(changed_text)
    else:
        output_body["objectId"] = "changed-body"
    generator = NoopPatchGenerator()
    executor = _executor(
        gateway=gateway,
        generator=generator,
    )

    with pytest.raises(OutputConflictError):
        await executor.execute(
            operation_id=uuid.uuid7(),
            request=_request(output_strategy=OutputStrategy.COPY),
        )

    assert gateway.created == 1
    assert gateway.requests == []
    assert gateway.completed == 0
    assert generator.calls == 0


@pytest.mark.parametrize(
    ("slide_count", "expected_error"),
    [
        pytest.param(0, UnsupportedSourceError, id="unsupported-structure"),
        pytest.param(
            MAX_PRESENTATION_SLIDES + 1,
            SourceTooLargeError,
            id="too-many-slides",
        ),
    ],
)
async def test_native_copy_parse_error_propagates_before_mutation(
    slide_count: int,
    expected_error: type[DocumentTransformError],
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    gateway.output_presentation["slides"] *= slide_count
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
    )

    with pytest.raises(expected_error):
        await executor.execute(
            operation_id=uuid.uuid7(),
            request=_request(output_strategy=OutputStrategy.COPY),
        )

    assert gateway.created == 1
    assert gateway.requests == []
    assert gateway.completed == 0


@pytest.mark.parametrize(
    "slide_count",
    [
        pytest.param(0, id="no-slides"),
        pytest.param(MAX_PRESENTATION_SLIDES + 1, id="exceeds-slide-limit"),
    ],
)
async def test_uneditable_pptx_conversion_returns_copy_for_review_without_mutation(
    slide_count: int,
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=PPTX_MIME_TYPE)
    gateway.output_presentation["slides"] *= slide_count
    generator = NoopPatchGenerator()
    executor = _executor(
        gateway=gateway,
        generator=generator,
    )

    result = await executor.execute(
        operation_id=uuid.uuid7(),
        request=_request(output_strategy=OutputStrategy.COPY),
    )

    assert gateway.created == 1
    assert gateway.requests == []
    assert gateway.completed == 0
    assert generator.calls == 0
    assert result.output.file_id == "output"
    assert result.changed is False
    assert result.needs_review is True
    assert result.validation.protected_content_preserved is False
    assert result.validation.structure_preserved is False
    assert result.applied_patch_count == 0
    assert {finding.code for finding in result.findings} == {"pptx_conversion_not_safely_editable"}


async def test_presentation_in_place_rejects_before_any_gateway_call(
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
    )

    with pytest.raises(InPlaceNotAllowedError):
        await executor.execute(
            operation_id=uuid.uuid7(),
            request=_request(output_strategy=OutputStrategy.IN_PLACE),
        )

    assert gateway.created == 0
    assert gateway.requests == []


async def test_exact_pending_commit_recovery_completes_without_reapplying(
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
    )
    first = await executor.execute(
        operation_id=uuid.uuid7(),
        request=_request(output_strategy=OutputStrategy.COPY),
    )
    gateway.created = 0
    gateway.completed = 0
    gateway.requests = []
    gateway.recovered = PresentationDraft(
        file_id=FileId("output"),
        name=first.output.name,
        parent_folder_id=FileId("folder"),
        revision_id=PresentationRevisionId("output-revision-2"),
        source_sha256=first.source_sha256,
        mutation=AppliedPresentationMutation(
            plan=PresentationMutationPlan(
                mutation_sha256="mutation-hash",
                base_revision_sha256="base-hash",
                expected_semantic_sha256="semantic-hash",
                expected_protected_structure_sha256="protected-hash",
                expected_brand_sha256=None,
            )
        ),
    )

    recovered = await executor.execute(
        operation_id=first.operation_id,
        request=_request(output_strategy=OutputStrategy.COPY),
    )

    assert gateway.created == 0
    assert gateway.requests == []
    assert gateway.completed == 1
    assert recovered.output.file_id == "output"
    assert recovered == first


async def test_known_post_write_verification_mismatch_is_not_completed(
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    gateway.verification_brand_applied = False
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
    )

    result = await executor.execute(
        operation_id=uuid.uuid7(),
        request=_request(output_strategy=OutputStrategy.COPY),
    )

    assert gateway.completed == 0
    assert result.needs_review
    assert "post_write_brand_mismatch" in {finding.code for finding in result.findings}


async def test_presentation_contextual_unslop_never_invokes_model(
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    assert isinstance(gateway.snapshot, NativePresentationSource)
    for presentation in (gateway.snapshot.presentation, gateway.output_presentation):
        text_run = json_object_at(
            presentation, "slides", 0, "pageElements", 0, "shape", "text", "textElements", 1
        )
        json_object_at(text_run, "textRun")["content"] = "This is a game changer.\n"
        text_run["endIndex"] = len("This is a game changer.\n")
    generator = NoopPatchGenerator()
    executor = _executor(
        gateway=gateway,
        generator=generator,
    )

    result = await executor.execute(
        operation_id=uuid.uuid7(),
        request=_request(output_strategy=OutputStrategy.COPY),
    )

    assert generator.calls == 0
    assert result.llm_invoked is False
    assert result.model_ids == []
    assert "contextual_unslop_review_required" in {finding.code for finding in result.findings}


async def test_native_mutation_limits_are_checked_before_copy(
    monkeypatch: pytest.MonkeyPatch,
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
    )

    def reject_oversized_plan(
        _presentation: dict[str, JsonValue], **_kwargs: Unpack[_SlidesPlanArguments]
    ) -> Never:
        raise SourceTooLargeError

    monkeypatch.setattr(
        "app.document_transform.presentation_executor.build_slides_mutation_plan",
        reject_oversized_plan,
    )

    with pytest.raises(SourceTooLargeError):
        await executor.execute(
            operation_id=uuid.uuid7(),
            request=_request(output_strategy=OutputStrategy.COPY),
        )

    assert gateway.created == 0


def _executor(
    *,
    gateway: PresentationGatewaySpy,
    generator: PatchGenerator | None = None,
    logo_uri: str = "https://wiki.sarj.ai/brand/sarj-logo-light.png",
    checkpoint_store: PresentationCheckpointStore | None = None,
    lease_token: uuid.UUID | None = None,
    checkpoint: DocumentTransformCheckpoint | None = None,
) -> PresentationTransformExecutor:
    store = PresentationCheckpointStore() if checkpoint_store is None else checkpoint_store
    return PresentationTransformExecutor(
        gateway=gateway,
        generator=NoopPatchGenerator() if generator is None else generator,
        logo_uri=logo_uri,
        checkpoint_store=store,
        lease_token=uuid.uuid7() if lease_token is None else lease_token,
        checkpoint=checkpoint,
        checkpoint_generation=len(store.checkpoints),
    )


def _request(*, output_strategy: OutputStrategy) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-04T00:00:00Z",
        ),
        mode=TransformMode.BOTH,
        output_strategy=output_strategy,
    )


def _presentation(presentation_id: str, revision_id: str) -> dict[str, Any]:
    text = "We approved the plan."
    return {
        "presentationId": presentation_id,
        "revisionId": revision_id,
        "title": "Review",
        "pageSize": {
            "width": {"magnitude": 720, "unit": "PT"},
            "height": {"magnitude": 405, "unit": "PT"},
        },
        "slides": [
            {
                "objectId": "slide-1",
                "pageElements": [
                    {
                        "objectId": "body-1",
                        "size": {
                            "width": {"magnitude": 500, "unit": "PT"},
                            "height": {"magnitude": 80, "unit": "PT"},
                        },
                        "transform": {
                            "translateX": 24,
                            "translateY": 120,
                            "unit": "PT",
                        },
                        "shape": {
                            "shapeType": "TEXT_BOX",
                            "placeholder": {"type": "BODY"},
                            "shapeProperties": {
                                "autofit": {"autofitType": "NONE"},
                                "shapeBackgroundFill": {"propertyState": "NOT_RENDERED"},
                            },
                            "text": {
                                "textElements": [
                                    {
                                        "startIndex": 0,
                                        "endIndex": 0,
                                        "paragraphMarker": {"style": {}},
                                    },
                                    {
                                        "startIndex": 0,
                                        "endIndex": len(text) + 1,
                                        "textRun": {
                                            "content": f"{text}\n",
                                            "style": {
                                                "weightedFontFamily": {
                                                    "fontFamily": CURRENT_BRAND_MANIFEST.document.font_family
                                                },
                                                "fontSize": {"magnitude": 20, "unit": "PT"},
                                                "foregroundColor": {
                                                    "opaqueColor": {
                                                        "rgbColor": {
                                                            "red": 0.1,
                                                            "green": 0.1,
                                                            "blue": 0.1,
                                                        }
                                                    }
                                                },
                                            },
                                        },
                                    },
                                ]
                            },
                        },
                    }
                ],
            }
        ],
    }


async def test_lost_creation_acknowledgement_never_creates_again() -> None:
    store = PresentationCheckpointStore()
    operation_id = uuid.uuid7()
    lease_token = uuid.uuid7()

    class LostAcknowledgementGateway(PresentationGatewaySpy):
        @override
        async def create_copy(
            self,
            *,
            operation_id: uuid.UUID,
            source: PresentationSourceSnapshot,
            source_sha256: str,
            output_name: str,
        ) -> PresentationDraft:
            assert isinstance(store.checkpoints[-1], CreatingPresentationCheckpoint)
            await super().create_copy(
                operation_id=operation_id,
                source=source,
                source_sha256=source_sha256,
                output_name=output_name,
            )
            raise TimeoutError

    gateway = LostAcknowledgementGateway(mime_type=MIME_SLIDE)
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="",
        checkpoint_store=store,
        lease_token=lease_token,
    )
    request = _request(output_strategy=OutputStrategy.COPY)
    with pytest.raises(ExternalEffectUncertainError):
        await executor.execute(operation_id=operation_id, request=request)
    assert gateway.created == 1
    assert len(store.checkpoints) == 2
    checkpoint = store.checkpoints[-1]
    assert isinstance(checkpoint, CreatingPresentationCheckpoint)
    resumed = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="",
        checkpoint_store=store,
        checkpoint=checkpoint,
    )
    with pytest.raises(ExternalEffectUncertainError):
        await resumed.execute(operation_id=operation_id, request=request)
    assert gateway.created == 1
    assert len(store.checkpoints) == 2

    gateway.recovered = PresentationDraft(
        file_id=FileId("output"),
        name=checkpoint.output_name,
        parent_folder_id=FileId("folder"),
        revision_id=PresentationRevisionId("output-revision"),
        source_sha256=checkpoint.source_sha256,
    )
    result = await resumed.execute(operation_id=operation_id, request=request)
    assert result.output.file_id == "output"
    assert gateway.created == 1
    assert store.checkpoints[-1].stage.value == "verified"


@pytest.mark.parametrize("fail_at_generation", [0, 1])
async def test_checkpoint_conflict_prevents_provider_creation(fail_at_generation: int) -> None:
    class ConflictingStore(PresentationCheckpointStore):
        @override
        async def save_document_transform_checkpoint(
            self,
            task_id: TaskId,
            *,
            lease_token: uuid.UUID,
            expected_generation: int,
            checkpoint: DocumentTransformCheckpoint,
        ) -> int:
            if expected_generation == fail_at_generation:
                raise OutputConflictError
            return await super().save_document_transform_checkpoint(
                task_id,
                lease_token=lease_token,
                expected_generation=expected_generation,
                checkpoint=checkpoint,
            )

    gateway = PresentationGatewaySpy(mime_type=MIME_SLIDE)
    executor = _executor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="",
        checkpoint_store=ConflictingStore(),
    )
    with pytest.raises(OutputConflictError):
        await executor.execute(
            operation_id=uuid.uuid7(), request=_request(output_strategy=OutputStrategy.COPY)
        )
    assert gateway.created == 0
