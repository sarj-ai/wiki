from copy import deepcopy
from typing import Any, Literal

import pytest

from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.native_table_font import (
    TableFontPlan,
    plan_table_fonts,
    project_table_fonts,
    table_font_requests,
)
from tests.test_document_transform import _branded_google_document
from tests.test_document_transform_repeat import _inspection_snapshot


def _document(text: str = "Alpha") -> dict[str, Any]:
    document = _branded_google_document()
    document["documentId"] = "source"
    end = 103 + len(text.encode("utf-16-le")) // 2 + 1
    paragraph = {
        "startIndex": 103,
        "endIndex": end,
        "paragraph": {
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": [
                {
                    "startIndex": 103,
                    "endIndex": end,
                    "textRun": {
                        "content": text + "\n",
                        "textStyle": {
                            "weightedFontFamily": {"fontFamily": "Arial"},
                            "bold": True,
                            "link": {"url": "https://example.test/private"},
                        },
                    },
                }
            ],
        },
    }
    table = {
        "startIndex": 100,
        "endIndex": end + 1,
        "table": {
            "rows": 1,
            "columns": 2,
            "tableStyle": {
                "tableColumnProperties": [
                    {"widthType": "FIXED_WIDTH", "columnWidth": {"unit": "PT", "magnitude": 120}}
                ]
            },
            "tableRows": [
                {
                    "startIndex": 101,
                    "endIndex": end + 1,
                    "tableCells": [
                        {
                            "startIndex": 102,
                            "endIndex": end + 1,
                            "tableCellStyle": {
                                "columnSpan": 2,
                                "rowSpan": 1,
                                "backgroundColor": {"color": {"rgbColor": {"red": 1}}},
                            },
                            "content": [paragraph],
                        }
                    ],
                }
            ],
        },
    }
    document["tabs"][0]["documentTab"]["body"]["content"].append(table)
    return document


def _paragraph(document: dict[str, Any]) -> dict[str, Any]:
    return document["tabs"][0]["documentTab"]["body"]["content"][-1]["table"]["tableRows"][0][
        "tableCells"
    ][0]["content"][0]


def _formatted(document: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(document)
    paragraph = _paragraph(result)
    run = paragraph["paragraph"]["elements"][0]
    terminator = deepcopy(run)
    run["endIndex"] -= 1
    run["textRun"]["content"] = run["textRun"]["content"].removesuffix("\n")
    run["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Nunito"
    terminator["startIndex"] = run["endIndex"]
    terminator["textRun"]["content"] = "\n"
    paragraph["paragraph"]["elements"].append(terminator)
    return result


@pytest.mark.parametrize("text", ["Alpha", "A", "😀A", "e\u0301x", "👩‍💻A"])
def test_table_family_only_projection_and_request_utf16_bounds(text: str) -> None:
    source = _document(text)
    plan = plan_table_fonts(source)
    assert len(plan.targets) == 1
    assert TableFontPlan.model_validate(plan.model_dump(mode="json")) == plan
    assert text not in plan.model_dump_json()
    assert "https://example.test/private" not in plan.model_dump_json()
    requests = table_font_requests(plan)
    assert requests[0]["updateTextStyle"]["range"]["startIndex"] == 103
    assert (
        requests[-1]["updateTextStyle"]["range"]["endIndex"] == _paragraph(source)["endIndex"] - 1
    )
    for request in requests:
        assert request["updateTextStyle"]["fields"] == "weightedFontFamily.fontFamily"
    actual = _formatted(source)
    assert project_table_fonts(actual, plan, applied=True) == project_table_fonts(source, plan)
    assert not table_font_requests(plan_table_fonts(actual))


@pytest.mark.parametrize(
    "change",
    [
        "bold",
        "link",
        "size",
        "weight",
        "unknown",
        "text",
        "index",
        "terminator",
        "merge",
        "parent",
        "missing",
        "duplicate",
    ],
)
def test_table_proof_rejects_every_non_font_change_and_incomplete_proof(
    change: Literal[
        "bold",
        "link",
        "size",
        "weight",
        "unknown",
        "text",
        "index",
        "terminator",
        "merge",
        "parent",
        "missing",
        "duplicate",
    ],
) -> None:
    source = _document()
    plan = plan_table_fonts(source)
    actual = _formatted(source)
    element = _paragraph(actual)
    run = element["paragraph"]["elements"][0]
    style = run["textRun"]["textStyle"]
    match change:
        case "bold":
            style["bold"] = False
        case "link":
            style["link"] = {"url": "https://example.test/changed"}
        case "size":
            style["fontSize"] = {"unit": "PT", "magnitude": 48}
        case "weight":
            style["weightedFontFamily"]["weight"] = 700
        case "unknown":
            style["unknown"] = True
        case "text":
            run["textRun"]["content"] = "Other"
        case "index":
            run["startIndex"] += 1
        case "terminator":
            element["paragraph"]["elements"][-1]["textRun"]["textStyle"]["weightedFontFamily"][
                "fontFamily"
            ] = "Nunito"
        case "merge":
            actual["tabs"][0]["documentTab"]["body"]["content"][-1]["table"]["tableRows"][0][
                "tableCells"
            ][0]["tableCellStyle"]["columnSpan"] = 1
        case "parent":
            actual["namedStyles"] = {"styles": []}
        case "missing":
            plan = plan.model_copy(update={"targets": ()})
        case "duplicate":
            plan = plan.model_copy(update={"targets": (*plan.targets, *plan.targets)})
    with pytest.raises(ValueError, match=r"^$"):
        project_table_fonts(actual, plan, applied=True)


@pytest.mark.parametrize(
    "change", ["bullet", "image", "suggestion", "unknown", "mono", "missing_font", "arabic"]
)
def test_unsupported_table_paragraphs_are_preserved_without_requests(
    change: Literal["bullet", "image", "suggestion", "unknown", "mono", "missing_font", "arabic"],
) -> None:
    source = _document("Arabic" if change != "arabic" else "مرحبا")
    paragraph = _paragraph(source)["paragraph"]
    run = paragraph["elements"][0]
    match change:
        case "bullet":
            paragraph["bullet"] = {"listId": "list-1"}
        case "image":
            run.pop("textRun")
            run["inlineObjectElement"] = {"inlineObjectId": "image-1"}
        case "suggestion":
            run["suggestedInsertionIds"] = ["suggestion-1"]
        case "unknown":
            paragraph["unknown"] = {}
        case "mono":
            run["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Courier New"
        case "missing_font":
            run["textRun"]["textStyle"].pop("weightedFontFamily")
        case "arabic":
            pass
    plan = plan_table_fonts(source)
    assert not plan.targets
    assert plan.skipped_paragraphs == 1
    assert not table_font_requests(plan)
    assert project_table_fonts(source, plan) == source


async def test_public_gateway_requires_explicit_table_proof_without_weakening_default() -> None:
    source = _document()
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    plan = plan_table_fonts(source)
    inspection = await gateway.inspect_in_place(
        snapshot=_inspection_snapshot(source), title="Decision", table_font_plan=plan
    )
    actual = _formatted(source)
    expected = parse_google_document(source, title="Decision")
    kwargs: dict[str, Any] = {
        "document": actual,
        "title": "Decision",
        "expected": expected,
        "structural_sha256": inspection.structural_sha256,
        "protected_structure_sha256": inspection.protected_structure_sha256,
        "verify_brand": True,
    }
    assert not (await gateway.verify_in_place(**kwargs)).structure_preserved
    verification = await gateway.verify_in_place(**kwargs, table_font_plan=plan)
    assert verification.content_preserved
    assert verification.structure_preserved
    assert verification.brand_applied


@pytest.mark.parametrize(
    "change",
    ["cell_bounds", "row_bounds", "unknown_table", "unknown_cell", "boolean_index", "overlap"],
)
def test_ambiguous_enclosing_topology_is_not_a_font_target(
    change: Literal[
        "cell_bounds", "row_bounds", "unknown_table", "unknown_cell", "boolean_index", "overlap"
    ],
) -> None:
    source = _document()
    table = source["tabs"][0]["documentTab"]["body"]["content"][-1]
    row = table["table"]["tableRows"][0]
    cell = row["tableCells"][0]
    match change:
        case "cell_bounds":
            cell["endIndex"] = 104
        case "row_bounds":
            row["startIndex"] = 200
        case "unknown_table":
            table["suggestedInsertionIds"] = ["private-suggestion"]
        case "unknown_cell":
            cell["unknown"] = {}
        case "boolean_index":
            cell["startIndex"] = True
        case "overlap":
            cell["content"].append(deepcopy(cell["content"][0]))
    plan = plan_table_fonts(source)
    assert not plan.targets
    assert plan.skipped_paragraphs > 0
    assert project_table_fonts(source, plan) == source


def test_preserved_nested_table_paragraphs_are_counted_without_flattening() -> None:
    source = _document()
    outer = source["tabs"][0]["documentTab"]["body"]["content"][-1]
    nested = deepcopy(outer)
    cell = outer["table"]["tableRows"][0]["tableCells"][0]
    cell["content"] = [nested]
    plan = plan_table_fonts(source)
    assert not plan.targets
    assert plan.skipped_paragraphs == 1
    assert project_table_fonts(source, plan) == source
