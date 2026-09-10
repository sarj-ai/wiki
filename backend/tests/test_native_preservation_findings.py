from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import DocumentGatewayError, InPlaceNeedsReviewError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import InPlaceStageResult
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.native_preservation import preserved_native_findings
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    FindingSeverity,
    InPlaceCheckpoint,
    TransformFinding,
    TransformMode,
)
from app.identifiers import DocsRevisionId, TaskId
from tests.test_document_transform import (
    _CheckpointStore,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
)
from tests.test_document_transform_header_provenance import _snapshot


def _native(*, protected: bool = False) -> dict[str, Any]:
    parts = [
        {
            "startIndex": 1,
            "endIndex": 7,
            "textRun": {"content": "Alpha ", "textStyle": {"bold": True}},
        },
        {
            "startIndex": 7,
            "endIndex": 12,
            "textRun": {"content": "Beta\n", "textStyle": {"bold": False}},
        },
    ]
    if protected:
        parts = [
            parts[0],
            {"startIndex": 7, "endIndex": 8, "pageBreak": {}},
            {"startIndex": 8, "endIndex": 9, "textRun": {"content": "\n"}},
        ]
    return {
        "revisionId": "revision",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 9 if protected else 12,
                                "paragraph": {"elements": parts},
                            }
                        ]
                    }
                },
            }
        ],
    }


def _finding() -> TransformFinding:
    return TransformFinding(
        code="google_doc_text_target_unsupported", block_id="b0", detail="Original warning"
    )


def test_legacy_finding_payload_defaults_to_review() -> None:
    finding = TransformFinding.model_validate(
        {"code": "legacy_warning", "block_id": None, "detail": "Review required"}
    )
    assert finding.severity is FindingSeverity.REVIEW
    assert "severity" not in finding.model_dump(mode="json")


def test_informational_finding_payload_persists_severity() -> None:
    finding = TransformFinding(
        code="native_rich_text_preserved",
        block_id="b0",
        detail="Rich text was preserved.",
        severity=FindingSeverity.INFORMATIONAL,
    )
    assert finding.model_dump(mode="json")["severity"] == "informational"


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
@pytest.mark.parametrize("font_family", ["Arial", "Nunito"])
def test_exact_preserved_blocks_receive_truthful_persistable_findings(
    *, protected: bool, mode: TransformMode, font_family: str
):
    native = _native(protected=protected)
    for part in native["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]:
        if "textRun" in part:
            part["textRun"].setdefault("textStyle", {})["weightedFontFamily"] = {
                "fontFamily": font_family
            }
    original = parse_google_document(native, title="Source")
    assert not original.blocks[0].editable
    result = preserved_native_findings(
        original=original,
        transformed=original,
        findings=(_finding(),),
        native_document=native,
        mode=mode,
    )
    assert result is not None
    assert result[0].code == "native_rich_text_preserved"
    assert result[0].block_id == "b0"
    assert result[0].severity is FindingSeverity.INFORMATIONAL
    assert "preserved" in result[0].detail
    if not protected:
        assert result[0].detail == (
            "Rich text wording and native structure were preserved; supported font formatting was checked."
        )
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    assert not any(
        "insertText" in request or "deleteContentRange" in request for request in requests
    )
    font_requests = [
        request["updateTextStyle"] for request in requests if "updateTextStyle" in request
    ]
    assert len(font_requests) == (0 if protected else 2)
    assert all(request["fields"] == "weightedFontFamily.fontFamily" for request in font_requests)


@pytest.mark.parametrize(
    "change",
    [
        "text",
        "partial_ranges",
        "missing_range",
        "wrong_tab",
        "unknown_finding",
        "unknown_block",
        "unslop",
    ],
)
def test_allowance_never_covers_unproven_or_modified_blocks(change: str):
    native = _native()
    original = parse_google_document(native, title="Source")
    transformed = original
    findings = (_finding(),)
    mode = TransformMode.FORMAT
    match change:
        case "text":
            transformed = replace(original, blocks=(replace(original.blocks[0], text="Changed"),))
        case "partial_ranges" | "missing_range":
            block = original.blocks[0]
            original = replace(
                original,
                blocks=(
                    replace(
                        block,
                        font_style_ranges=block.font_style_ranges[:1]
                        if change == "partial_ranges"
                        else (),
                    ),
                ),
            )
            transformed = original
        case "wrong_tab":
            native["tabs"][0]["tabProperties"]["tabId"] = "other"
        case "unknown_finding":
            findings = (TransformFinding(code="other_warning", block_id="b0", detail="Unrelated"),)
        case "unknown_block":
            findings = (_finding().model_copy(update={"block_id": "other"}),)
        case _:
            mode = TransformMode.UNSLOP
    assert (
        preserved_native_findings(
            original=original,
            transformed=transformed,
            findings=findings,
            native_document=native,
            mode=mode,
        )
        is None
    )


async def test_existing_protected_checkpoint_hash_rejects_native_element_tampering():
    original = _native(protected=True)
    original["documentId"] = "source"
    expected = parse_google_document(original, title="Source")
    changed = deepcopy(original)
    changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][1][
        "pageBreak"
    ] = {"textStyle": {"bold": True}}
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    inspection = await gateway.inspect_in_place(snapshot=_snapshot(original), title="Source")
    result = await gateway.verify_in_place(
        document=changed,
        title="Source",
        expected=expected,
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=False,
    )
    assert result.content_preserved
    assert not result.structure_preserved


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
@pytest.mark.parametrize("legacy_components", [False, True])
async def test_in_place_preservation_receipt_survives_interruption_and_completed_replay(
    monkeypatch: pytest.MonkeyPatch,
    *,
    protected: bool,
    mode: TransformMode,
    legacy_components: bool,
):
    gateway = _InPlaceGateway("Source")
    document = _native(protected=protected)
    document["documentId"] = "source"
    document["revisionId"] = "revision-1"
    gateway.document = document
    assert gateway.snapshot is not None
    gateway.snapshot = replace(gateway.snapshot, google_document=document, web_url=None)
    store = _CheckpointStore(gateway.events)
    operation, task = uuid.uuid7(), TaskId(uuid.uuid7())
    attempts: list[object] = []

    async def apply(**kwargs: object) -> InPlaceStageResult:
        attempts.append(kwargs["requests"])
        if len(attempts) == 1:
            raise DocumentGatewayError
        return InPlaceStageResult(
            revision_id=DocsRevisionId("revision-1"), document=deepcopy(document)
        )

    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)
    request = _in_place_request(mode)

    def executor(
        checkpoint: DocumentTransformCheckpoint | None = None,
    ) -> DocumentTransformExecutor:
        return DocumentTransformExecutor(
            gateway=gateway,
            generator=_Generator(),
            checkpoint_store=store,
            task_id=task,
            lease_token=uuid.uuid7(),
            checkpoint=checkpoint,
            checkpoint_generation=store.generation,
        )

    with pytest.raises(DocumentGatewayError):
        await executor().execute(operation_id=operation, request=request)
    checkpoint = store.checkpoints[-1]
    if legacy_components:
        payload = checkpoint.model_dump(mode="json")
        for proof in payload["native_font_proof"] or []:
            proof.pop("components", None)
        checkpoint = InPlaceCheckpoint.model_validate(payload)
    assert checkpoint.findings[0].code == "native_rich_text_preserved"
    result = await executor(checkpoint).execute(operation_id=operation, request=request)
    assert result.findings == checkpoint.findings
    assert result.findings[0].severity is FindingSeverity.INFORMATIONAL
    assert not result.needs_review
    assert attempts[0] == attempts[1]
    completed = executor(store.checkpoints[-1])
    assert await completed.execute(operation_id=operation, request=request) == result
    assert len(attempts) == 2
    missing_proof = store.checkpoints[-1].model_copy(update={"native_font_proof": None})
    with pytest.raises(InPlaceNeedsReviewError):
        await executor(missing_proof).execute(operation_id=operation, request=request)
    assert len(attempts) == 2


@pytest.mark.parametrize("stage", ["content", "headers"])
async def test_persisted_font_proof_rejects_provider_style_loss_even_when_gateway_verifies(
    monkeypatch: pytest.MonkeyPatch, stage: str
):
    gateway = _InPlaceGateway("Source", missing_header=stage == "headers")
    document = _native()
    document["documentId"] = "source"
    document["revisionId"] = "revision-1"
    gateway.document = document
    assert gateway.snapshot is not None
    gateway.snapshot = replace(gateway.snapshot, google_document=document, web_url=None)
    store = _CheckpointStore(gateway.events)

    async def apply(**_kwargs: object) -> InPlaceStageResult:
        changed = deepcopy(document)
        changed["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0][
            "textRun"
        ]["textStyle"]["bold"] = False
        return InPlaceStageResult(revision_id=DocsRevisionId("revision-1"), document=changed)

    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)
    executor = DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    )
    with pytest.raises(InPlaceNeedsReviewError):
        await executor.execute(
            operation_id=uuid.uuid7(), request=_in_place_request(TransformMode.FORMAT)
        )
    assert store.checkpoints[-1].stage.value != "complete"
