from copy import deepcopy
from dataclasses import replace
from typing import Any
import uuid

import pytest

from app.document_transform.custom_furniture import (
    CustomFurnitureBinding,
    custom_furniture_requests_safe,
)
from app.document_transform.errors import DocumentGatewayError, InPlaceNeedsReviewError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import (
    InPlaceInspection,
    InPlaceStageResult,
    OutputVerification,
    SourceSnapshot,
)
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
)
from app.identifiers import DocsRevisionId, FileId, TaskId
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _Gateway,
    _Generator,
)
from tests.test_document_transform_repeat import _inspection_snapshot
from tests.test_native_font_proof import _formatted
from tests.test_native_preservation_findings import _native


def _custom_document(*, first_even: bool = False, footer: bool = False) -> dict[str, Any]:
    document = _branded_google_document()
    document["documentId"] = "source"
    tab = document["tabs"][0]["documentTab"]
    header = tab["headers"]["header-a"]
    header["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] = (
        "Authored custom header\n"
    )
    tab["documentStyle"]["marginLeft"] = {"magnitude": 96, "unit": "PT"}
    if first_even:
        for field, identifier in (
            ("firstPageHeaderId", "header-first"),
            ("evenPageHeaderId", "header-even"),
        ):
            tab["documentStyle"][field] = identifier
            tab["headers"][identifier] = deepcopy(header)
    if footer:
        tab["documentStyle"]["defaultFooterId"] = "footer-custom"
        tab["footers"] = {
            "footer-custom": {
                "content": [
                    {"paragraph": {"elements": [{"textRun": {"content": "Authored footer\n"}}]}}
                ]
            }
        }
    return document


def _native_gateway() -> GoogleDocumentGateway:
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://wiki.sarj.ai/brand/sarj-logo-light.png"
    return gateway


async def _verify_preserved(
    document: dict[str, Any], inspection: InPlaceInspection, expected_document: dict[str, Any]
) -> OutputVerification:
    return await _native_gateway().verify_in_place(
        document=document,
        title="Decision",
        expected=parse_google_document(expected_document, title="Decision"),
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=True,
        custom_furniture=inspection.custom_furniture,
    )


async def test_preserved_footer_never_exempts_producer_header_brand_proof() -> None:
    document = _custom_document(footer=True)
    document["tabs"][0]["documentTab"]["headers"] = deepcopy(
        _branded_google_document()["tabs"][0]["documentTab"]["headers"]
    )
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    assert inspection.custom_furniture[0].header_ids == ()
    assert inspection.supported_brand_applied
    expected_document = deepcopy(document)
    document["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0]["paragraph"][
        "elements"
    ][0]["textRun"]["textStyle"]["fontSize"]["magnitude"] = 48
    verification = await _verify_preserved(document, inspection, expected_document)
    assert not verification.brand_applied
    assert not verification.supported_brand_applied


@pytest.mark.parametrize(
    "field", ["defaultHeaderId", "firstPageHeaderId", "evenPageHeaderId", "defaultFooterId"]
)
@pytest.mark.parametrize(
    "value", [None, False, [], "missing"], ids=["null", "boolean", "list", "dangling"]
)
def test_malformed_or_dangling_furniture_bindings_remain_unknown(field: str, value: object) -> None:
    document = _custom_document(first_even=True, footer=True)
    document["tabs"][0]["documentTab"]["documentStyle"][field] = value
    inspection = _native_gateway()._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    )
    assert inspection.unknown_header_tab_ids
    assert not inspection.custom_furniture


@pytest.mark.parametrize("first_even", [False, True])
@pytest.mark.parametrize("footer", [False, True])
async def test_custom_furniture_is_preserved_not_owned_or_full_brand(
    *, first_even: bool, footer: bool
) -> None:
    document = _custom_document(first_even=first_even, footer=footer)
    before = deepcopy(document)
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    assert not inspection.headers
    assert not inspection.preserved_headers
    assert not inspection.unknown_header_tab_ids
    assert not inspection.fully_branded
    assert inspection.supported_brand_applied
    assert len(inspection.custom_furniture) == 1
    verification = await _verify_preserved(document, inspection, before)
    assert verification.content_preserved
    assert verification.structure_preserved
    assert not verification.brand_applied
    assert verification.supported_brand_applied
    assert document == before


@pytest.mark.parametrize(
    "change", ["header", "footer", "image", "geometry", "named_style", "binding", "extra_segment"]
)
async def test_custom_furniture_verification_rejects_payload_or_context_changes(
    change: str,
) -> None:
    document = _custom_document(first_even=True, footer=True)
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    expected_document = deepcopy(document)
    tab = document["tabs"][0]["documentTab"]
    if change == "header":
        tab["headers"]["header-even"]["content"] = []
    elif change == "footer":
        tab["footers"]["footer-custom"]["content"] = []
    elif change == "image":
        tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"]["size"]["width"][
            "magnitude"
        ] = 1
    elif change == "geometry":
        tab["documentStyle"]["marginLeft"]["magnitude"] = 1
    elif change == "named_style":
        tab["namedStyles"] = {
            "styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {"italic": True}}]
        }
    elif change == "binding":
        tab["documentStyle"]["evenPageHeaderId"] = "header-first"
    else:
        tab["headers"]["unknown"] = deepcopy(tab["headers"]["header-a"])
    verification = await _verify_preserved(document, inspection, expected_document)
    assert not verification.structure_preserved
    assert not verification.supported_brand_applied


@pytest.mark.parametrize(
    "mutation",
    [
        {"updateTextStyle": {"range": {"segmentId": "header-a"}}},
        {"insertText": {"endOfSegmentLocation": {"segmentId": "footer-custom"}}},
        {"updateDocumentStyle": {"tabId": "tab-a"}},
        {"updateDocumentStyle": {}},
        {"createHeader": {"sectionBreakLocation": {"tabId": "tab-a"}}},
        {"deleteHeader": {"headerId": "header-a"}},
        {"updateNamedStyles": {}},
    ],
    ids=[
        "header-text",
        "footer-text",
        "tab-geometry",
        "global-geometry",
        "create-header",
        "delete-header",
        "named-styles",
    ],
)
def test_checkpoint_request_guard_rejects_furniture_or_global_writes(
    mutation: dict[str, Any],
) -> None:
    document = _custom_document(footer=True)
    inspection = _native_gateway()._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    )
    assert not custom_furniture_requests_safe([mutation], inspection.custom_furniture)


@pytest.mark.parametrize(
    ("outage", "tamper", "rich"),
    [(False, False, False), (True, False, False), (True, True, False), (True, False, True)],
)
async def test_custom_body_format_then_repeat_is_noop_with_truthful_receipt(
    monkeypatch: pytest.MonkeyPatch, *, outage: bool, tamper: bool, rich: bool
) -> None:
    document = _custom_document(first_even=True, footer=True)
    branded_body = deepcopy(document["tabs"][0]["documentTab"]["body"])
    document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"][0]["textRun"][
        "textStyle"
    ]["weightedFontFamily"]["fontFamily"] = "Arial"
    if rich:
        rich_document = _native()
        document["tabs"][0]["documentTab"]["body"] = rich_document["tabs"][0]["documentTab"]["body"]
        branded_body = _formatted(rich_document)["tabs"][0]["documentTab"]["body"]
    native = _native_gateway()
    gateway = _Gateway(_inspection_snapshot(document))
    events: list[str] = []
    store = _CheckpointStore(events)
    task_id = TaskId(uuid.uuid7())
    operation = uuid.uuid7()
    before = native._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    ).custom_furniture

    async def load(_source: DriveFileSource) -> SourceSnapshot:
        return replace(
            _inspection_snapshot(document), revision_id=DocsRevisionId(document["revisionId"])
        )

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        if events.count("body") == 0:
            assert kwargs["required_revision_id"] == document["revisionId"]
            assert custom_furniture_requests_safe(kwargs["requests"], before)
            assert not any("updateDocumentStyle" in item for item in kwargs["requests"])
            document["tabs"][0]["documentTab"]["body"] = deepcopy(branded_body)
            document["revisionId"] = "formatted"
            events.append("body")
            if outage:
                raise DocumentGatewayError
        return InPlaceStageResult(DocsRevisionId(document["revisionId"]), deepcopy(document))

    monkeypatch.setattr(gateway, "load_drive_source", load)
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    monkeypatch.setattr(gateway, "verify_in_place", native.verify_in_place)
    monkeypatch.setattr(gateway, "apply_in_place_stage", apply)
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )

    def executor(checkpoint: Any = None) -> DocumentTransformExecutor:
        return DocumentTransformExecutor(
            gateway=gateway,
            generator=_Generator(),
            checkpoint_store=store,
            task_id=task_id,
            lease_token=uuid.uuid7(),
            checkpoint=checkpoint,
            checkpoint_generation=store.generation,
        )

    if outage:
        with pytest.raises(DocumentGatewayError):
            await executor().execute(operation_id=operation, request=request)
    if tamper:
        document["tabs"][0]["documentTab"]["headers"]["header-a"]["content"] = []
        with pytest.raises(InPlaceNeedsReviewError):
            await executor(store.checkpoints[-1]).execute(operation_id=operation, request=request)
        assert events.count("body") == 1
        return
    result = await executor(store.checkpoints[-1] if outage else None).execute(
        operation_id=operation, request=request
    )
    assert result.changed
    assert result.needs_review
    assert [finding.code for finding in result.findings] == (
        ["native_rich_text_preserved", "custom_furniture_preserved"]
        if rich
        else ["custom_furniture_preserved"]
    )
    assert events.count("body") == 1
    assert (
        native._inspect_in_place_sync(_inspection_snapshot(document), "Decision").custom_furniture
        == before
    )
    replay = await executor(store.checkpoints[-1]).execute(operation_id=operation, request=request)
    assert replay == result
    again = await executor().execute(operation_id=uuid.uuid7(), request=request)
    assert not again.changed
    assert again.needs_review
    assert events.count("body") == 1


@pytest.mark.parametrize("value", [None, False, [], ""], ids=["null", "bool", "list", "empty"])
def test_malformed_binding_with_no_segments_is_not_treated_as_missing(value: object) -> None:
    document = _custom_document()
    tab = document["tabs"][0]["documentTab"]
    tab["headers"] = {}
    tab["documentStyle"]["defaultHeaderId"] = value
    inspection = _native_gateway()._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    )
    assert inspection.unknown_header_tab_ids
    assert not inspection.missing_header_tab_ids
    assert not inspection.custom_furniture


def test_preserved_section_context_is_immutable() -> None:
    document = _custom_document()
    tab = document["tabs"][0]["documentTab"]
    tab["body"]["content"].insert(
        0, {"sectionBreak": {"sectionStyle": {"columnSeparatorStyle": "NONE"}}}
    )
    gateway = _native_gateway()
    before = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    tab["body"]["content"][0]["sectionBreak"]["sectionStyle"]["columnSeparatorStyle"] = (
        "BETWEEN_EACH_COLUMN"
    )
    after = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    assert before.custom_furniture
    assert before.custom_furniture != after.custom_furniture


def test_preserved_evidence_round_trips_through_json_database_payload() -> None:
    document = _custom_document(first_even=True, footer=True)
    evidence = (
        _native_gateway()
        ._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
        .custom_furniture[0]
    )
    assert CustomFurnitureBinding.model_validate(evidence.model_dump(mode="json")) == evidence


async def test_full_brand_with_existing_footer_remains_clean_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _custom_document(footer=True)
    tab = document["tabs"][0]["documentTab"]
    branded_tab = _branded_google_document()["tabs"][0]["documentTab"]
    tab["headers"] = deepcopy(branded_tab["headers"])
    tab["documentStyle"] = {
        **deepcopy(branded_tab["documentStyle"]),
        "defaultFooterId": "footer-custom",
    }
    native = _native_gateway()
    inspection = native._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    assert inspection.fully_branded
    assert inspection.custom_furniture
    gateway = _Gateway(_inspection_snapshot(document))
    monkeypatch.setattr(gateway, "inspect_in_place", native.inspect_in_place)
    result = await DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=_CheckpointStore([]),
        task_id=TaskId(uuid.uuid7()),
        lease_token=uuid.uuid7(),
    ).execute(
        operation_id=uuid.uuid7(),
        request=DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file", file_id=FileId("source"), expected_modified_time="now"
            ),
            mode=TransformMode.FORMAT,
            output_strategy=OutputStrategy.IN_PLACE,
        ),
    )
    assert not result.changed
    assert not result.needs_review
    assert not result.findings


@pytest.mark.parametrize("field", ["firstPageHeaderId", "defaultFooterId"])
def test_branded_header_does_not_mask_dangling_furniture_reference(field: str) -> None:
    document = _branded_google_document()
    document["documentId"] = "source"
    document["tabs"][0]["documentTab"]["documentStyle"][field] = "missing"
    inspection = _native_gateway()._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    )
    assert inspection.unknown_header_tab_ids
    assert not inspection.custom_furniture


@pytest.mark.parametrize("field", ["namedStyles", "documentStyle"])
def test_root_inherited_context_is_bound_even_with_explicit_tab_context(field: str) -> None:
    document = _custom_document()
    document["tabs"][0]["documentTab"]["namedStyles"] = {"styles": []}
    document[field] = {"known": "before"}
    gateway = _native_gateway()
    before = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    document[field] = {"known": "after"}
    after = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    assert before.custom_furniture
    assert before.custom_furniture != after.custom_furniture
