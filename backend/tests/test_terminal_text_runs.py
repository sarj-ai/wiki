from copy import deepcopy
from typing import Any

import pytest

from app.document_transform.ir import parse_google_document, utf16_length


def _document(text: str = "Context:") -> dict[str, Any]:
    end = 1 + utf16_length(text)
    return {
        "revisionId": "r1",
        "tabs": [
            {
                "tabProperties": {"tabId": "t1"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": end + 1,
                                "paragraph": {
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": end,
                                            "textRun": {
                                                "content": text,
                                                "textStyle": {"bold": True},
                                            },
                                        },
                                        {
                                            "startIndex": end,
                                            "endIndex": end + 1,
                                            "textRun": {
                                                "content": "\n",
                                                "textStyle": {"bold": False},
                                            },
                                        },
                                    ],
                                },
                            }
                        ]
                    }
                },
            }
        ],
    }


@pytest.mark.parametrize("text", ["Context:", "Context 🙂:"])
def test_exact_terminal_run_preserves_text_styles_and_utf16_range(text: str) -> None:
    document = _document(text)
    before = deepcopy(document)
    block = parse_google_document(document, title="Review").blocks[0]
    assert block.editable
    assert block.text == text
    assert block.is_authored_lead_in
    assert block.source_range is not None
    assert block.source_range.end_index == 1 + utf16_length(text)
    assert document == before


def test_non_latin_content_with_valid_terminal_indices_stays_locked() -> None:
    document = _document("سياق:")
    block = parse_google_document(document, title="Review").blocks[0]
    assert block.text == "سياق:"
    assert block.source_range is not None
    assert block.source_range.end_index == 1 + utf16_length("سياق:")
    assert not block.editable


@pytest.mark.parametrize(
    "change",
    [
        "gap",
        "overlap",
        "wrong_end",
        "missing_index",
        "boolean_index",
        "string_index",
        "negative_index",
        "boolean_paragraph",
        "negative_paragraph",
        "paragraph_end",
        "utf16_mismatch",
        "extra_run",
        "content_run",
        "link",
        "newline_link",
        "suggestion",
        "newline_suggestion",
        "paragraph_suggestion",
        "chip",
        "code",
        "newline_code",
        "non_latin",
        "unknown_run_field",
        "unknown_text_field",
    ],
)
def test_non_equivalent_or_protected_terminal_runs_stay_locked(change: str) -> None:
    document = _document("Context 🙂:")
    element = document["tabs"][0]["documentTab"]["body"]["content"][0]
    paragraph = element["paragraph"]
    runs = paragraph["elements"]
    match change:
        case "gap":
            runs[1]["startIndex"] += 1
        case "overlap":
            runs[1]["startIndex"] -= 1
        case "wrong_end":
            runs[1]["endIndex"] += 1
        case "missing_index":
            runs[0].pop("startIndex")
        case "boolean_index":
            runs[0]["startIndex"] = True
        case "string_index":
            runs[0]["startIndex"] = "1"
        case "negative_index":
            runs[0]["startIndex"] = -1
        case "boolean_paragraph":
            element["startIndex"] = True
        case "negative_paragraph":
            element["startIndex"] = -1
        case "paragraph_end":
            element["endIndex"] += 1
        case "utf16_mismatch":
            runs[0]["endIndex"] -= 1
        case "extra_run":
            runs.append(deepcopy(runs[1]))
        case "content_run":
            runs[1]["textRun"]["content"] = "More\n"
        case "link" | "newline_link":
            runs[int(change == "newline_link")]["textRun"]["textStyle"]["link"] = {
                "url": "https://example.com"
            }
        case "suggestion" | "newline_suggestion":
            runs[int(change == "newline_suggestion")]["suggestedInsertionIds"] = ["s1"]
        case "paragraph_suggestion":
            paragraph["suggestedParagraphStyleChanges"] = {"s1": {}}
        case "chip":
            runs.append({"inlineObjectElement": {"inlineObjectId": "object"}})
        case "code" | "newline_code":
            runs[int(change == "newline_code")]["textRun"]["textStyle"]["weightedFontFamily"] = {
                "fontFamily": "Courier New"
            }
        case "unknown_run_field":
            runs[0]["unknownContent"] = {}
        case "unknown_text_field":
            runs[0]["textRun"]["unknownContent"] = {}
        case _:
            runs[0]["textRun"]["content"] = "سياق:"
    assert not parse_google_document(document, title="Review").blocks[0].editable


@pytest.mark.parametrize(
    "text", ["Body\n", "Body\r", "Body\v", "Body\f", "Body\u2028", "Body\u2029"]
)
def test_content_run_with_embedded_terminator_stays_locked(text: str) -> None:
    assert not parse_google_document(_document(text), title="Review").blocks[0].editable
