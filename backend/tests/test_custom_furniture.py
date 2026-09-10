from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any
import uuid

import pytest

from app.document_transform.brand import (
    SarjBrandProfile,
    build_custom_header_title_requests,
)
from app.document_transform.brand_manifest import HEADER_TITLE_SEPARATOR
from app.document_transform.custom_furniture import (
    CustomFurnitureBinding,
    CustomHeaderTitleTarget,
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
from app.document_transform.ir import DocumentIR, parse_google_document, utf16_length
from app.document_transform.native_visual_authority import (
    apply_visual_authority,
    visual_authority_block_ids,
)
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
)
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId, TaskId
from tests.test_document_transform import (
    _branded_google_document,
    _CheckpointStore,
    _Gateway,
    _Generator,
)
from tests.test_document_transform_repeat import _inspection_snapshot
from tests.test_native_font_proof import _formatted
from tests.test_native_preservation_findings import _native


@dataclass(frozen=True, slots=True)
class _CustomHeaderPlan:
    inspection: InPlaceInspection
    requests: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _ProviderNormalizedHeaders:
    before: dict[str, Any]
    document: dict[str, Any]
    inspection: InPlaceInspection


def _terminal_newline_run(elements: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        element["textRun"]
        for element in elements
        if element.get("textRun", {}).get("content", "").endswith("\n")
    )


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


def _apply_header_title_requests(
    document: dict[str, Any],
    requests: tuple[dict[str, Any], ...],
    *,
    simulate_terminal_newline_inheritance: bool = False,
) -> None:
    tabs = {tab["tabProperties"]["tabId"]: tab["documentTab"] for tab in document["tabs"]}
    styles = {
        (
            request["updateTextStyle"]["range"]["tabId"],
            request["updateTextStyle"]["range"]["segmentId"],
        ): request["updateTextStyle"]["textStyle"]
        for request in requests
        if "updateTextStyle" in request and request["updateTextStyle"]["range"]["startIndex"] == 0
    }
    for request in requests:
        if "insertText" not in request:
            continue
        location = request["insertText"]["location"]
        tab = tabs[location["tabId"]]
        elements = tab["headers"][location["segmentId"]]["content"][0]["paragraph"]["elements"]
        if (
            simulate_terminal_newline_inheritance
            and (terminal_run := _terminal_newline_run(elements)).get("content") == "\n"
        ):
            terminal_run["textStyle"] = deepcopy(styles[location["tabId"], location["segmentId"]])
        elements.insert(
            0,
            {
                "textRun": {
                    "content": request["insertText"]["text"],
                    "textStyle": deepcopy(styles[location["tabId"], location["segmentId"]]),
                }
            },
        )
    for request in requests:
        update = request.get("updateTextStyle")
        if not isinstance(update, dict) or update.get("fields") != "*":
            continue
        range_value = update["range"]
        elements = tabs[range_value["tabId"]]["headers"][range_value["segmentId"]]["content"][0][
            "paragraph"
        ]["elements"]
        _terminal_newline_run(elements)["textStyle"] = deepcopy(update["textStyle"])


def _custom_header_plan(
    native: GoogleDocumentGateway, document: dict[str, Any]
) -> _CustomHeaderPlan:
    inspection = native._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    return _CustomHeaderPlan(
        inspection=inspection,
        requests=tuple(
            build_custom_header_title_requests(
                "Decision",
                profile=SarjBrandProfile(),
                headers=inspection.custom_header_title_targets,
            )
        ),
    )


def _provider_normalized_custom_headers() -> _ProviderNormalizedHeaders:
    document = _custom_document(first_even=True, footer=True)
    tab = document["tabs"][0]["documentTab"]
    normal_style = {
        "weightedFontFamily": {"fontFamily": "Helvetica"},
        "fontSize": {"magnitude": 12, "unit": "PT"},
        "bold": False,
        "italic": False,
        "underline": False,
    }
    tab["namedStyles"]["styles"][0]["textStyle"] = deepcopy(normal_style)
    header_styles = {
        "header-a": normal_style,
        "header-first": {
            "weightedFontFamily": {"fontFamily": "DIN Next LT Arabic"},
            "fontSize": {"magnitude": 10.5, "unit": "PT"},
        },
        "header-even": normal_style,
    }
    for header_id, style in header_styles.items():
        header = tab["headers"][header_id]
        header["content"][0]["startIndex"] = 0
        header["content"][0]["endIndex"] = 3
        header["content"][0]["paragraph"]["elements"] = [
            {
                "startIndex": 1,
                "endIndex": 3,
                "textRun": {"content": "\ue000\n", "textStyle": deepcopy(style)},
            }
        ]
    footer = tab["footers"]["footer-custom"]
    footer["content"][0]["startIndex"] = 0
    footer["content"][0]["endIndex"] = 17
    footer["content"][0]["paragraph"]["elements"][0].update({"startIndex": 1, "endIndex": 17})
    before = deepcopy(document)
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    requests = tuple(
        build_custom_header_title_requests(
            "Decision",
            profile=SarjBrandProfile(),
            headers=inspection.custom_header_title_targets,
        )
    )
    _apply_header_title_requests(document, requests)
    title_length = utf16_length(
        next(request["insertText"]["text"] for request in requests if "insertText" in request)
    )
    for header_id, original_style in header_styles.items():
        header = tab["headers"][header_id]
        header["content"][0]["endIndex"] += title_length
        elements = header["content"][0]["paragraph"]["elements"]
        elements[0].update({"startIndex": 0, "endIndex": title_length})
        newline_style = (
            deepcopy(original_style)
            if header_id == "header-first"
            else {"weightedFontFamily": {"fontFamily": "Helvetica"}}
        )
        elements[1:] = [
            {
                "startIndex": title_length + 1,
                "endIndex": title_length + 2,
                "textRun": {"content": "\ue000", "textStyle": deepcopy(original_style)},
            },
            {
                "startIndex": title_length + 2,
                "endIndex": title_length + 3,
                "textRun": {"content": "\n", "textStyle": newline_style},
            },
        ]
    return _ProviderNormalizedHeaders(before, document, inspection)


def _native_gateway() -> GoogleDocumentGateway:
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://wiki.sarj.ai/brand/sarj-logo-light.png"
    return gateway


async def _verify_preserved(
    document: dict[str, Any], inspection: InPlaceInspection, expected_document: dict[str, Any]
) -> OutputVerification:
    return await _verify_custom_brand(
        document,
        inspection,
        parse_google_document(expected_document, title="Decision"),
    )


async def _verify_custom_brand(
    document: dict[str, Any],
    inspection: InPlaceInspection,
    expected: DocumentIR,
    *,
    custom_header_titles: tuple[CustomHeaderTitleTarget, ...] = (),
) -> OutputVerification:
    return await _native_gateway().verify_in_place(
        document=document,
        title="Decision",
        expected=expected,
        structural_sha256=inspection.structural_sha256,
        protected_structure_sha256=inspection.protected_structure_sha256,
        verify_brand=True,
        custom_furniture=inspection.custom_furniture,
        custom_header_titles=custom_header_titles,
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
    assert not inspection.supported_brand_applied
    assert len(inspection.custom_header_title_targets) == (3 if first_even else 1)
    assert len(inspection.custom_furniture) == 1
    verification = await _verify_preserved(document, inspection, before)
    assert verification.content_preserved
    assert verification.structure_preserved
    assert not verification.brand_applied
    assert not verification.supported_brand_applied
    assert document == before


@pytest.mark.asyncio
@pytest.mark.parametrize("first_even", [False, True])
async def test_custom_header_title_prefix_is_native_idempotent_and_preserves_suffix(
    first_even: bool,
) -> None:
    document = _custom_document(first_even=first_even, footer=True)
    for header in document["tabs"][0]["documentTab"]["headers"].values():
        header["content"][0]["paragraph"]["elements"][0]["textRun"]["content"] = "\ue000\n"
    before = deepcopy(document)
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    requests = tuple(
        build_custom_header_title_requests(
            "Decision",
            profile=SarjBrandProfile(),
            headers=inspection.custom_header_title_targets,
        )
    )

    assert custom_furniture_requests_safe(
        list(requests),
        inspection.custom_furniture,
        allowed_header_requests=requests,
    )
    _apply_header_title_requests(document, requests)
    after = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")

    assert not after.custom_header_title_targets
    assert after.custom_header_titles_present == inspection.custom_header_title_targets
    assert after.custom_furniture == inspection.custom_furniture
    assert after.supported_brand_applied
    verification = await _verify_custom_brand(
        document,
        inspection,
        parse_google_document(before, title="Decision"),
        custom_header_titles=inspection.custom_header_title_targets,
    )
    assert verification.structure_preserved
    assert verification.supported_brand_applied


@pytest.mark.asyncio
async def test_custom_header_title_restores_empty_header_terminal_newline_style() -> None:
    document = _custom_document(first_even=True, footer=True)
    empty_header = document["tabs"][0]["documentTab"]["headers"]["header-first"]
    newline_style = {
        "weightedFontFamily": {"fontFamily": "Helvetica Neue", "weight": 400},
        "fontSize": {"magnitude": 12, "unit": "PT"},
    }
    empty_run = empty_header["content"][0]["paragraph"]["elements"][0]["textRun"]
    empty_run["content"] = "\n"
    empty_run["textStyle"] = deepcopy(newline_style)
    empty_element = empty_header["content"][0]["paragraph"]["elements"][0]
    empty_element["startIndex"] = 0
    empty_element["endIndex"] = 1
    before = deepcopy(document)
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    requests = tuple(
        build_custom_header_title_requests(
            "Decision",
            profile=SarjBrandProfile(),
            headers=inspection.custom_header_title_targets,
        )
    )

    assert any(
        request.get("updateTextStyle", {}).get("fields") == "*"
        and request["updateTextStyle"]["range"]["segmentId"] == "header-first"
        and request["updateTextStyle"]["textStyle"] == newline_style
        for request in requests
    )
    assert custom_furniture_requests_safe(
        list(requests),
        inspection.custom_furniture,
        allowed_header_requests=requests,
    )
    tampered_requests = deepcopy(list(requests))
    restore_request = next(
        request
        for request in tampered_requests
        if request.get("updateTextStyle", {}).get("fields") == "*"
        and request["updateTextStyle"]["range"]["segmentId"] == "header-first"
    )
    restore_request["updateTextStyle"]["textStyle"]["fontSize"]["magnitude"] = 9
    assert not custom_furniture_requests_safe(
        tampered_requests,
        inspection.custom_furniture,
        allowed_header_requests=requests,
    )
    _apply_header_title_requests(
        document,
        requests,
        simulate_terminal_newline_inheritance=True,
    )

    restored_run = _terminal_newline_run(
        document["tabs"][0]["documentTab"]["headers"]["header-first"]["content"][0]["paragraph"][
            "elements"
        ]
    )
    assert restored_run["textStyle"] == newline_style
    after = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    assert after.custom_header_titles_present == inspection.custom_header_title_targets
    assert after.custom_furniture == inspection.custom_furniture
    verification = await _verify_custom_brand(
        document,
        inspection,
        parse_google_document(before, title="Decision"),
        custom_header_titles=inspection.custom_header_title_targets,
    )
    assert verification.structure_preserved
    assert verification.supported_brand_applied


@pytest.mark.asyncio
async def test_custom_header_projection_accepts_only_effective_provider_normalization() -> None:
    case = _provider_normalized_custom_headers()
    before, document, inspection = case.before, case.document, case.inspection
    after = _native_gateway()._inspect_in_place_sync(_inspection_snapshot(document), "Decision")

    assert {(target.tab_id, target.header_id) for target in after.custom_header_titles_present} == {
        (target.tab_id, target.header_id) for target in inspection.custom_header_title_targets
    }
    assert after.custom_furniture == inspection.custom_furniture
    verification = await _verify_custom_brand(
        document,
        inspection,
        parse_google_document(before, title="Decision"),
        custom_header_titles=inspection.custom_header_title_targets,
    )
    assert verification.structure_preserved
    assert verification.supported_brand_applied


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    ["terminal_style", "suffix_text", "nonterminal_style", "paragraph", "footer"],
)
async def test_custom_header_projection_rejects_authored_furniture_changes(tamper: str) -> None:
    case = _provider_normalized_custom_headers()
    before, document, inspection = case.before, case.document, case.inspection
    tab = document["tabs"][0]["documentTab"]
    header = tab["headers"]["header-even"]
    paragraph = header["content"][0]["paragraph"]
    suffix, newline = paragraph["elements"][-2:]
    match tamper:
        case "terminal_style":
            newline["textRun"]["textStyle"]["fontSize"] = {"magnitude": 9, "unit": "PT"}
        case "suffix_text":
            suffix["textRun"]["content"] = "X"
        case "nonterminal_style":
            suffix["textRun"]["textStyle"]["bold"] = True
        case "paragraph":
            paragraph["paragraphStyle"]["alignment"] = "START"
        case _:
            tab["footers"]["footer-custom"]["content"][0]["paragraph"]["elements"][0]["textRun"][
                "content"
            ] = "Changed footer\n"

    verification = await _verify_custom_brand(
        document,
        inspection,
        parse_google_document(before, title="Decision"),
        custom_header_titles=inspection.custom_header_title_targets,
    )

    assert not verification.structure_preserved
    assert not verification.supported_brand_applied


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["title", "style", "suffix"])
async def test_custom_header_title_proof_rejects_tampering(tamper: str) -> None:
    document = _custom_document()
    before = deepcopy(document)
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    requests = tuple(
        build_custom_header_title_requests(
            "Decision",
            profile=SarjBrandProfile(),
            headers=(
                CustomHeaderTitleTarget(
                    tab_id=DocsTabId("tab-a"), header_id=DocsSegmentId("header-a")
                ),
            ),
        )
    )
    _apply_header_title_requests(document, requests)
    runs = document["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0]["paragraph"][
        "elements"
    ]
    match tamper:
        case "title":
            runs[0]["textRun"]["content"] = "Different" + HEADER_TITLE_SEPARATOR
        case "style":
            runs[0]["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Arial"
        case _:
            runs[1]["textRun"]["content"] = "Changed suffix\n"

    verification = await _verify_custom_brand(
        document,
        inspection,
        parse_google_document(before, title="Decision"),
        custom_header_titles=inspection.custom_header_title_targets,
    )
    assert not verification.supported_brand_applied


@pytest.mark.parametrize("tamper", ["text", "index", "style"])
def test_custom_header_title_request_guard_requires_the_exact_plan(tamper: str) -> None:
    document = _custom_document()
    inspection = _native_gateway()._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    )
    expected = tuple(
        build_custom_header_title_requests(
            "Decision",
            profile=SarjBrandProfile(),
            headers=(
                CustomHeaderTitleTarget(
                    tab_id=DocsTabId("tab-a"), header_id=DocsSegmentId("header-a")
                ),
            ),
        )
    )
    requests = deepcopy(list(expected))
    match tamper:
        case "text":
            requests[0]["insertText"]["text"] = "Different"
        case "index":
            requests[0]["insertText"]["location"]["index"] = 1
        case _:
            requests[1]["updateTextStyle"]["textStyle"]["bold"] = False

    assert not custom_furniture_requests_safe(
        requests,
        inspection.custom_furniture,
        allowed_header_requests=expected,
    )


async def test_custom_furniture_accepts_preserved_visual_authority() -> None:
    document = _custom_document()
    body_paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    body_paragraph["paragraphStyle"]["alignment"] = "CENTER"
    body_paragraph["elements"][0]["textRun"]["textStyle"]["fontSize"] = {
        "magnitude": 18,
        "unit": "PT",
    }
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")
    parsed = parse_google_document(document, title="Decision")
    preserved_ids = visual_authority_block_ids(document, parsed)
    assert preserved_ids == ("b0",)
    expected = apply_visual_authority(parsed, preserved_ids)

    verification = await _verify_custom_brand(document, inspection, expected)

    assert verification.content_preserved
    assert verification.structure_preserved
    assert not verification.brand_applied
    assert not verification.supported_brand_applied


async def test_custom_furniture_does_not_exempt_unpreserved_authored_body_style() -> None:
    document = _custom_document()
    body_paragraph = document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    body_paragraph["paragraphStyle"]["alignment"] = "CENTER"
    gateway = _native_gateway()
    inspection = gateway._inspect_in_place_sync(_inspection_snapshot(document), "Decision")

    verification = await _verify_preserved(document, inspection, document)

    assert not verification.supported_brand_applied


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
    match change:
        case "header":
            tab["headers"]["header-even"]["content"] = []
        case "footer":
            tab["footers"]["footer-custom"]["content"] = []
        case "image":
            tab["inlineObjects"]["logo-1"]["inlineObjectProperties"]["embeddedObject"]["size"][
                "width"
            ]["magnitude"] = 1
        case "geometry":
            tab["documentStyle"]["marginLeft"]["magnitude"] = 1
        case "named_style":
            tab["namedStyles"] = {
                "styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {"italic": True}}]
            }
        case "binding":
            tab["documentStyle"]["evenPageHeaderId"] = "header-first"
        case _:
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
        for part in rich_document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"][
            "elements"
        ]:
            part["textRun"]["textStyle"]["weightedFontFamily"] = {
                "fontFamily": "Arial",
                "weight": 400,
            }
        document["tabs"][0]["documentTab"]["body"] = rich_document["tabs"][0]["documentTab"]["body"]
        branded_body = _formatted(rich_document)["tabs"][0]["documentTab"]["body"]
        for part in branded_body["content"][0]["paragraph"]["elements"]:
            part["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Nunito"
            part["textRun"]["textStyle"]["weightedFontFamily"]["weight"] = 400
    native = _native_gateway()
    gateway = _Gateway(_inspection_snapshot(document))
    events: list[str] = []
    store = _CheckpointStore(events)
    task_id = TaskId(uuid.uuid7())
    header_plan = _custom_header_plan(native, document)

    async def load(_source: DriveFileSource) -> SourceSnapshot:
        return replace(
            _inspection_snapshot(document), revision_id=DocsRevisionId(document["revisionId"])
        )

    async def apply(**kwargs: Any) -> InPlaceStageResult:
        if events.count("body") == 0:
            assert kwargs["required_revision_id"] == document["revisionId"]
            assert custom_furniture_requests_safe(
                kwargs["requests"],
                header_plan.inspection.custom_furniture,
                allowed_header_requests=header_plan.requests,
            )
            assert not any("updateDocumentStyle" in item for item in kwargs["requests"])
            _apply_header_title_requests(document, header_plan.requests)
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
            await executor().execute(operation_id=task_id, request=request)
    if tamper:
        document["tabs"][0]["documentTab"]["headers"]["header-a"]["content"] = []
        with pytest.raises(InPlaceNeedsReviewError):
            await executor(store.checkpoints[-1]).execute(operation_id=task_id, request=request)
        assert events.count("body") == 1
        return
    result = await executor(store.checkpoints[-1] if outage else None).execute(
        operation_id=task_id, request=request
    )
    assert result.changed
    assert not result.needs_review
    assert [finding.code for finding in result.findings] == (
        ["native_rich_text_preserved", "custom_furniture_preserved"]
        if rich
        else ["custom_furniture_preserved"]
    )
    assert all(finding.severity.value == "informational" for finding in result.findings)
    assert events.count("body") == 1
    assert not native._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    ).custom_header_title_targets
    assert native._inspect_in_place_sync(
        _inspection_snapshot(document), "Decision"
    ).custom_header_titles_present == (header_plan.inspection.custom_header_title_targets)
    for header in document["tabs"][0]["documentTab"]["headers"].values():
        text = "".join(
            item.get("textRun", {}).get("content", "")
            for item in header["content"][0]["paragraph"]["elements"]
        )
        assert text.count("Decision") == 1
    assert (
        native._inspect_in_place_sync(_inspection_snapshot(document), "Decision").custom_furniture
        == header_plan.inspection.custom_furniture
    )
    replay = await executor(store.checkpoints[-1]).execute(operation_id=task_id, request=request)
    assert replay == result
    completed = store.checkpoints[-1]
    again = await executor().execute(operation_id=uuid.uuid7(), request=request)
    assert not again.changed
    assert not again.needs_review
    assert events.count("body") == 1
    document["tabs"][0]["documentTab"]["headers"]["header-a"]["content"][0]["paragraph"][
        "elements"
    ][0]["textRun"]["content"] = "Tampered" + HEADER_TITLE_SEPARATOR
    with pytest.raises(InPlaceNeedsReviewError):
        await executor(completed).execute(operation_id=task_id, request=request)


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
