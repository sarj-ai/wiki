from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import DocumentGatewayError
from app.document_transform.executor import (
    IN_PLACE_REQUEST_BUILDER_VERSION,
    DocumentTransformExecutor,
)
from app.document_transform.gateway import InPlaceStageResult
from app.document_transform.ir import parse_google_document
from app.document_transform.native_font_noop import filter_redundant_native_fonts
from app.document_transform.schemas import DocumentTransformCheckpoint, TransformMode
from app.identifiers import DocsRevisionId, TaskId
from tests.test_document_transform import (
    _CheckpointStore,
    _Generator,
    _in_place_request,
    _InPlaceGateway,
)
from tests.test_native_preservation_findings import _native


def _document() -> dict[str, Any]:
    document = _native()
    for part in _parts(document):
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    return document


def _parts(document: dict[str, Any]) -> list[dict[str, Any]]:
    return document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]


@pytest.mark.parametrize("astral", [False, True])
@pytest.mark.parametrize("weight", [None, 100, 400, 700, 900])
def test_explicit_family_noops_removed_without_changing_other_requests_or_source(
    *, astral: bool, weight: int | None
) -> None:
    document = _document()
    if weight is not None:
        for part in _parts(document):
            part["textRun"]["textStyle"]["weightedFontFamily"]["weight"] = weight
    if astral:
        _parts(document)[0]["textRun"]["content"] = "😀pha "
    before = deepcopy(document)
    original = parse_google_document(document, title="Source")
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    families = [
        item
        for item in requests
        if item.get("updateTextStyle", {}).get("fields") == "weightedFontFamily.fontFamily"
    ]
    assert len(families) == 2
    assert all(
        item["updateTextStyle"]["textStyle"] == {"weightedFontFamily": {"fontFamily": "Nunito"}}
        for item in families
    )
    filtered = filter_redundant_native_fonts(
        requests, document=document, original=original, transformed=original, font_family="Nunito"
    )
    assert filtered == [item for item in requests if item not in families]
    assert document == before
    assert (
        filter_redundant_native_fonts(
            filtered,
            document=document,
            original=original,
            transformed=original,
            font_family="Nunito",
        )
        == filtered
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "different",
        "unknown",
        "suggestion",
        "gap",
        "boolean",
        "text",
        "duplicate",
        "partial",
        "transformed",
        "segment",
        "wrong_mask",
        "request_boolean",
    ],
)
def test_ambiguous_or_changed_payload_keeps_all_requests(change: str) -> None:
    document = _document()
    original = parse_google_document(document, title="Source")
    transformed = original
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    match change:
        case "missing":
            del _parts(document)[0]["textRun"]["textStyle"]["weightedFontFamily"]
        case "different":
            _parts(document)[0]["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = (
                "Arial"
            )
        case "unknown":
            _parts(document)[0]["textRun"]["textStyle"]["unknown"] = True
        case "suggestion":
            _parts(document)[0]["suggestedInsertionIds"] = ["pending"]
        case "gap":
            _parts(document)[1]["startIndex"] += 1
        case "boolean":
            _parts(document)[0]["startIndex"] = True
        case "text":
            _parts(document)[0]["textRun"]["content"] = "Other "
        case "duplicate":
            document["tabs"].append(deepcopy(document["tabs"][0]))
        case "partial":
            block = original.blocks[0]
            original = replace(
                original, blocks=(replace(block, font_style_ranges=block.font_style_ranges[:1]),)
            )
            transformed = original
        case "transformed":
            transformed = original.replace_blocks({original.blocks[0].id: "Different text"})
        case "segment" | "wrong_mask" | "request_boolean":
            for item in requests:
                if item.get("updateTextStyle", {}).get("fields") == "weightedFontFamily.fontFamily":
                    match change:
                        case "segment":
                            item["updateTextStyle"]["range"]["segmentId"] = "header"
                        case "wrong_mask":
                            item["updateTextStyle"]["fields"] = "weightedFontFamily"
                        case _:
                            item["updateTextStyle"]["range"]["startIndex"] = True
        case _:
            raise AssertionError
    assert (
        filter_redundant_native_fonts(
            requests,
            document=document,
            original=original,
            transformed=transformed,
            font_family="Nunito",
        )
        == requests
    )


@pytest.mark.parametrize(
    "value",
    [None, True, False, 99, 150, 901, 400.0, "400", {}, []],
    ids=[
        "null",
        "true",
        "false",
        "below",
        "not-hundred",
        "above",
        "float",
        "string",
        "object",
        "array",
    ],
)
def test_invalid_explicit_weight_keeps_family_requests(value: object) -> None:
    document = _document()
    original = parse_google_document(document, title="Source")
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    _parts(document)[0]["textRun"]["textStyle"]["weightedFontFamily"]["weight"] = value
    assert (
        filter_redundant_native_fonts(
            requests,
            document=document,
            original=original,
            transformed=original,
            font_family="Nunito",
        )
        == requests
    )


def test_unknown_family_property_keeps_family_requests() -> None:
    document = _document()
    original = parse_google_document(document, title="Source")
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    _parts(document)[0]["textRun"]["textStyle"]["weightedFontFamily"]["unknown"] = 400
    assert (
        filter_redundant_native_fonts(
            requests,
            document=document,
            original=original,
            transformed=original,
            font_family="Nunito",
        )
        == requests
    )


def test_preceding_edit_cannot_collide_with_another_paragraphs_old_ranges() -> None:
    document = _document()
    body = document["tabs"][0]["documentTab"]["body"]["content"]
    first = body[0]
    width = first["endIndex"] - first["startIndex"]
    prefix_text = "x" * (width + 3) + "\n"
    for value in [first, *first["paragraph"]["elements"]]:
        value["startIndex"] += len(prefix_text)
        value["endIndex"] += len(prefix_text)
    second = deepcopy(first)
    for value in [second, *second["paragraph"]["elements"]]:
        value["startIndex"] += width
        value["endIndex"] += width
    for part in second["paragraph"]["elements"]:
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Arial"}
    body[:] = [
        {
            "startIndex": 1,
            "endIndex": 1 + len(prefix_text),
            "paragraph": {
                "elements": [
                    {
                        "startIndex": 1,
                        "endIndex": 1 + len(prefix_text),
                        "textRun": {"content": prefix_text},
                    }
                ]
            },
        },
        first,
        second,
    ]
    original = parse_google_document(document, title="Source")
    transformed = original.replace_blocks({original.blocks[0].id: "xxx"})
    requests = build_copy_mutations(
        original,
        transformed,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri="https://example.test/logo.png",
    )
    old_span = original.blocks[1].font_style_ranges[0]
    assert any(
        item.get("updateTextStyle", {}).get("range")
        == {
            "startIndex": old_span.start_index,
            "endIndex": old_span.end_index,
            "tabId": old_span.tab_id,
        }
        for item in requests
    )
    assert (
        filter_redundant_native_fonts(
            requests,
            document=document,
            original=original,
            transformed=transformed,
            font_family="Nunito",
        )
        == requests
    )


@pytest.mark.parametrize("missing_header", [False, True])
@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
async def test_new_content_plan_filters_but_persisted_replay_remains_exact(
    monkeypatch: pytest.MonkeyPatch, *, missing_header: bool, mode: TransformMode
) -> None:
    gateway = _InPlaceGateway("Source", missing_header=missing_header)
    document = _document()
    document["documentId"] = "source"
    document["revisionId"] = "revision-1"
    gateway.document = document
    assert gateway.snapshot is not None
    gateway.snapshot = replace(gateway.snapshot, google_document=document, web_url=None)
    store = _CheckpointStore(gateway.events)
    operation, task = uuid.uuid7(), TaskId(uuid.uuid7())
    attempts: list[dict[str, Any]] = []

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        if any("createHeader" in item for item in kwargs["requests"]):
            gateway.header_created = True
            return InPlaceStageResult(DocsRevisionId("revision-1"), deepcopy(document))
        attempts.append(deepcopy(kwargs))
        assert not any(
            item.get("updateTextStyle", {}).get("fields") == "weightedFontFamily.fontFamily"
            for item in kwargs["requests"]
        )
        if len(attempts) == 1:
            raise DocumentGatewayError
        return InPlaceStageResult(DocsRevisionId("revision-1"), deepcopy(document))

    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)

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

    request = _in_place_request(mode)
    with pytest.raises(DocumentGatewayError):
        await executor().execute(operation_id=operation, request=request)
    checkpoint = store.checkpoints[-1]
    assert checkpoint.request_builder_version == IN_PLACE_REQUEST_BUILDER_VERSION
    result = await executor(checkpoint).execute(operation_id=operation, request=request)
    assert attempts[0] == attempts[1]
    assert (
        await executor(store.checkpoints[-1]).execute(operation_id=operation, request=request)
        == result
    )
    assert len(attempts) == 2
