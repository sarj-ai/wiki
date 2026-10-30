from copy import deepcopy
from typing import Any

import pytest

from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.markdown_import import verify_markdown_import
from tests.test_markdown_import import _branded_document


def _footer_runs(document: dict[str, Any]) -> list[dict[str, Any]]:
    return document["tabs"][0]["documentTab"]["footers"]["footer-1"]["content"][0]["paragraph"][
        "elements"
    ]


def _indexed_footer(*, newline: bool) -> dict[str, Any]:
    document = _branded_document()
    runs = _footer_runs(document)
    runs[0].update(startIndex=0, endIndex=7)
    runs[1].update(startIndex=7, endIndex=8)
    if newline:
        runs.append({"startIndex": 8, "endIndex": 9, "textRun": {"content": "\n", "textStyle": {}}})
    return document


def _paragraph_wrapper(document: dict[str, Any]) -> dict[str, Any]:
    return document["tabs"][0]["documentTab"]["footers"]["footer-1"]["content"][0]


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("newline", [False, True])
def test_exact_native_footer_allows_location_metadata_and_single_terminal_newline(
    *, indexed: bool, newline: bool
) -> None:
    document = _indexed_footer(newline=newline)
    _paragraph_wrapper(document).update(startIndex=0, endIndex=9 if newline else 8)
    if not indexed:
        for run in _footer_runs(document):
            run.pop("startIndex")
            run.pop("endIndex")
    before = deepcopy(document)
    result = verify_markdown_import(
        document, markdown_import_manifest("Body", title="Import", branded=True)
    )
    assert result.brand_applied
    assert result.content_preserved
    assert result.structure_preserved
    assert document == before


@pytest.mark.parametrize("omitted", ["startIndex", "endIndex"])
def test_footer_partial_indices_preserve_proven_adjacency(omitted: str) -> None:
    document = _indexed_footer(newline=True)
    _paragraph_wrapper(document).update(startIndex=0, endIndex=9)
    _footer_runs(document)[1].pop(omitted)
    result = verify_markdown_import(
        document, markdown_import_manifest("Body", title="Import", branded=True)
    )
    assert result.brand_applied
    assert result.content_preserved
    assert result.structure_preserved


@pytest.mark.parametrize(
    "change",
    [
        "foreign_text",
        "reversed",
        "duplicate_page",
        "wrong_page",
        "extra_newline",
        "early_newline",
        "space",
        "crlf",
        "unknown_wrapper",
        "both_variants",
        "boolean_index",
        "negative_index",
        "string_index",
        "reversed_index",
        "gap",
        "wrong_width",
        "unknown_payload",
        "suggestion",
        "newline_link",
        "newline_suggestion",
        "malformed_style",
        "unknown_style",
        "extra_paragraph",
        "outer_end",
        "paragraph_suggestion",
        "shifted_contiguous_tail",
    ],
)
def test_footer_proof_rejects_extra_protected_or_malformed_content(change: str) -> None:
    document = _indexed_footer(newline=True)
    runs = _footer_runs(document)
    match change:
        case "foreign_text":
            runs[0]["textRun"]["content"] = "Foreign"
        case "reversed":
            runs.reverse()
        case "duplicate_page":
            runs.insert(2, deepcopy(runs[1]))
        case "wrong_page":
            runs[1]["autoText"]["type"] = "PAGE_COUNT"
        case "extra_newline":
            runs.append(deepcopy(runs[-1]))
        case "early_newline":
            runs.insert(0, runs.pop())
        case "space" | "crlf":
            runs[-1]["textRun"]["content"] = " " if change == "space" else "\r\n"
        case "unknown_wrapper":
            runs[0]["unknown"] = {}
        case "both_variants":
            runs[0]["autoText"] = {"type": "PAGE_NUMBER"}
        case "boolean_index" | "negative_index" | "string_index" | "reversed_index":
            runs[0]["startIndex"] = {
                "boolean_index": True,
                "negative_index": -1,
                "string_index": "0",
                "reversed_index": 8,
            }[change]
        case "gap":
            runs[1]["startIndex"] = 8
        case "wrong_width":
            runs[0]["endIndex"] = 6
        case "unknown_payload":
            runs[0]["textRun"]["unknown"] = {}
        case "suggestion" | "newline_suggestion":
            runs[-1 if change == "newline_suggestion" else 0]["textRun"][
                "suggestedInsertionIds"
            ] = ["synthetic"]
        case "newline_link":
            runs[-1]["textRun"]["textStyle"]["link"] = {"url": "https://example.com"}
        case "malformed_style":
            runs[-1]["textRun"]["textStyle"] = []
        case "unknown_style":
            runs[-1]["textRun"]["textStyle"]["unknown"] = True
        case "outer_end":
            _paragraph_wrapper(document).update(startIndex=0, endIndex=10)
        case "paragraph_suggestion":
            _paragraph_wrapper(document)["paragraph"]["suggestedParagraphStyleChanges"] = {
                "synthetic": {}
            }
        case "shifted_contiguous_tail":
            for run in runs[1:]:
                run["startIndex"] += 1
                run["endIndex"] += 1
        case _:
            document["tabs"][0]["documentTab"]["footers"]["footer-1"]["content"].append(
                {"paragraph": {"elements": []}}
            )
    result = verify_markdown_import(
        document, markdown_import_manifest("Body", title="Import", branded=True)
    )
    assert not result.brand_applied
    assert result.content_preserved
    assert result.structure_preserved
