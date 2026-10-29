from __future__ import annotations

from copy import deepcopy
from typing import Any, override
import uuid

import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import (
    DocumentTransformError,
    InPlaceNotAllowedError,
    OutputConflictError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.google_presentation_gateway import PPTX_MIME_TYPE
from app.document_transform.models import PatchGenerator
from app.document_transform.presentation_executor import PresentationTransformExecutor
from app.document_transform.presentation_gateway import (
    DriveFileVersion,
    PresentationDraft,
    PresentationRevisionId,
    PresentationSourceSnapshot,
    PresentationVerification,
)
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.slides_ir import MAX_PRESENTATION_SLIDES
from app.drive.schemas import MIME_SLIDE
from app.identifiers import FileId


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
        self.snapshot = PresentationSourceSnapshot(
            file_id=FileId("source"),
            title="Review",
            source_name="Review.pptx" if mime_type == PPTX_MIME_TYPE else "Review",
            parent_folder_id=FileId("folder"),
            modified_time="2026-09-04T00:00:00Z",
            revision_id=(
                PresentationRevisionId("source-revision") if mime_type == MIME_SLIDE else None
            ),
            drive_version=DriveFileVersion("7"),
            mime_type=mime_type,
            size=1_024 if mime_type == PPTX_MIME_TYPE else None,
            md5_checksum="a" * 32 if mime_type == PPTX_MIME_TYPE else None,
            can_download=mime_type == PPTX_MIME_TYPE,
            can_copy=mime_type == MIME_SLIDE,
            presentation=source_presentation if mime_type == MIME_SLIDE else None,
        )
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
        del mutation_sha256, expected_semantic_sha256
        del expected_protected_structure_sha256, expected_brand_sha256
        self.requests = requests
        self.required_revision_id = required_revision_id
        return PresentationDraft(
            file_id=draft.file_id,
            name=draft.name,
            parent_folder_id=draft.parent_folder_id,
            revision_id=PresentationRevisionId("output-revision-2"),
            source_sha256=draft.source_sha256,
            web_url=draft.web_url,
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
        del draft, title
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


@pytest.mark.parametrize("mime_type", [MIME_SLIDE, PPTX_MIME_TYPE])
async def test_presentation_copy_is_revision_fenced_branded_and_keeps_source(
    mime_type: str,
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=mime_type)
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
        text_run["textRun"]["content"] = changed_text
        text_run["endIndex"] = len(changed_text)
    else:
        output_body["objectId"] = "changed-body"
    generator = NoopPatchGenerator()
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=generator,
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=generator,
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
        applied_mutation_sha256="mutation-hash",
        pending_mutation_sha256="mutation-hash",
    )

    recovered = await executor.execute(
        operation_id=uuid.uuid7(),
        request=_request(output_strategy=OutputStrategy.COPY),
    )

    assert gateway.created == 0
    assert gateway.requests == []
    assert gateway.completed == 1
    assert recovered.output.file_id == "output"
    assert {finding.code for finding in recovered.findings} == {"recovered_completed_output"}


async def test_known_post_write_verification_mismatch_is_not_completed(
    presentation_gateway_factory: type[PresentationGatewaySpy],
) -> None:
    gateway = presentation_gateway_factory(mime_type=MIME_SLIDE)
    gateway.verification_brand_applied = False
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
    for presentation in (gateway.snapshot.presentation, gateway.output_presentation):
        assert presentation is not None
        text_run = presentation["slides"][0]["pageElements"][0]["shape"]["text"]["textElements"][1]
        text_run["textRun"]["content"] = "This is a game changer.\n"
        text_run["endIndex"] = len("This is a game changer.\n")
    generator = NoopPatchGenerator()
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=generator,
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
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
    executor = PresentationTransformExecutor(
        gateway=gateway,
        generator=NoopPatchGenerator(),
        logo_uri="https://wiki.sarj.ai/brand/sarj-logo-light.png",
    )

    def reject_oversized_plan(*args: object, **kwargs: object) -> object:
        del args, kwargs
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
