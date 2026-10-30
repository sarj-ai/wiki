from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from app.document_transform import google_gateway as gateway
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_font_defense import defend_native_font_requests


def _document(runs: list[dict[str, Any]]) -> dict[str, Any]:
    index = 1
    elements = []
    for run in runs:
        content = run["textRun"]["content"]
        end = index + utf16_length(content)
        elements.append({"startIndex": index, "endIndex": end, **run})
        index = end
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
                                "endIndex": index,
                                "paragraph": {"elements": elements},
                            }
                        ]
                    }
                },
            }
        ],
    }


def _run(text: str, **style: object) -> dict[str, Any]:
    return {"textRun": {"content": text, "textStyle": style}}


def _font_requests(raw: dict[str, Any]) -> list[dict[str, Any]]:
    document = parse_google_document(raw, title="Report")
    requests = build_copy_mutations(
        document, document, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    return [request["updateTextStyle"] for request in requests if "updateTextStyle" in request]


@pytest.mark.parametrize(
    "emphasis",
    [{"bold": True}, {"italic": True}, {"underline": True}, {"bold": False}],
    ids=["bold", "italic", "underline", "explicit-normal"],
)
def test_uniform_authored_body_emphasis_survives_brand_and_repeat(
    emphasis: dict[str, bool],
) -> None:
    raw = _document([_run("A concise authored lead-in.\n", **emphasis)])
    before = parse_google_document(raw, title="Report")
    requests = _font_requests(raw)
    assert len(requests) == 1
    assert all(requests[0]["textStyle"][key] is value for key, value in emphasis.items())
    paragraph = raw["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["elements"][0]["textRun"]["textStyle"].update(requests[0]["textStyle"])
    assert _font_requests(raw) == requests
    assert parse_google_document(raw, title="Report").semantic_sha256 == before.semantic_sha256
    assert before.blocks[0].is_authored_lead_in is (emphasis.get("bold") is True)


def test_unstyled_uppercase_label_is_not_promoted_or_bolded() -> None:
    raw = _document([_run("REVIEW CHECKS\n")])
    request = _font_requests(raw)[0]
    assert "bold" not in request["fields"]
    assert not parse_google_document(raw, title="Report").blocks[0].is_authored_lead_in


def test_short_authored_bold_label_keeps_accent_without_becoming_heading() -> None:
    raw = _document([_run("REVIEW CHECKS\n", bold=True)])
    parsed = parse_google_document(raw, title="Report")
    requests = build_copy_mutations(
        parsed, parsed, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    paragraph_style = next(
        item["updateParagraphStyle"]["paragraphStyle"]
        for item in requests
        if "updateParagraphStyle" in item
    )
    text_style = _font_requests(raw)[0]["textStyle"]
    assert paragraph_style["namedStyleType"] == "NORMAL_TEXT"
    assert paragraph_style["keepWithNext"] is False
    assert (
        text_style["foregroundColor"]
        != _font_requests(_document([_run("Ordinary prose.\n")]))[0]["textStyle"]["foregroundColor"]
    )


def test_mixed_authored_emphasis_stays_run_scoped() -> None:
    raw = _document([_run("Strong lead-in. ", bold=True), _run("Ordinary prose.\n", italic=True)])
    assert not parse_google_document(raw, title="Report").blocks[0].authored_emphasis
    assert all(
        request["fields"] == "weightedFontFamily.fontFamily" for request in _font_requests(raw)
    )


def test_linked_emphasized_prose_gets_font_without_rewriting_attributes() -> None:
    raw = _document(
        [
            _run("Read ", bold=True),
            _run("our review\n", link={"url": "https://example.com"}, italic=True),
        ]
    )
    before = deepcopy(raw)
    parsed = parse_google_document(raw, title="Report")
    assert not parsed.blocks[0].editable
    requests = _font_requests(raw)
    assert len(requests) == 2
    assert all(item["fields"] == "weightedFontFamily.fontFamily" for item in requests)
    assert all(
        item["textStyle"] == {"weightedFontFamily": {"fontFamily": "Nunito"}} for item in requests
    )
    assert requests[-1]["range"]["endIndex"] == 16
    assert raw == before


@pytest.mark.parametrize("font", ["Roboto Mono", "Fira Code", "Cambria Math", "Symbol"])
def test_code_and_math_fonts_are_preserved(font: str) -> None:
    raw = _document([_run("Prose "), _run("x = 1\n", weightedFontFamily={"fontFamily": font})])
    requests = _font_requests(raw)
    assert len(requests) == 1
    assert requests[0]["range"]["endIndex"] == 7


@pytest.mark.parametrize("text", ["مرحبا", "日本語", "κόσμος"])
def test_script_specific_typefaces_are_preserved(text: str) -> None:
    raw = _document([_run(f"{text}\n", weightedFontFamily={"fontFamily": "Noto Sans"})])
    assert not parse_google_document(raw, title="Report").blocks[0].editable
    assert _font_requests(raw) == []


def test_suggested_runs_and_superscript_are_preserved() -> None:
    suggested = _run("Suggestion ")
    suggested["textRun"]["suggestedTextStyleChanges"] = {"id": {"textStyle": {"bold": True}}}
    raw = _document([suggested, _run("2", baselineOffset="SUPERSCRIPT"), _run(" prose\n")])
    requests = _font_requests(raw)
    assert len(requests) == 1
    assert requests[0]["range"]["startIndex"] == 13


def test_object_paragraph_and_malformed_run_ranges_are_not_font_styled() -> None:
    raw = _document([_run("Linked\n", link={"url": "https://example.com"})])
    paragraph = raw["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    paragraph["elements"][0]["endIndex"] += 1
    assert _font_requests(raw) == []
    paragraph["elements"].append({"inlineObjectElement": {"inlineObjectId": "image"}})
    assert _font_requests(raw) == []


def test_font_ranges_rebase_after_earlier_text_edits_using_utf16_offsets() -> None:
    raw = _document([_run("Old\n")])
    second = _document([_run("Link\n", link={"url": "https://example.com"})])
    content = raw["tabs"][0]["documentTab"]["body"]["content"]
    next_paragraph = second["tabs"][0]["documentTab"]["body"]["content"][0]
    next_paragraph["startIndex"] += 4
    next_paragraph["endIndex"] += 4
    for element in next_paragraph["paragraph"]["elements"]:
        element["startIndex"] += 4
        element["endIndex"] += 4
    content.append(next_paragraph)
    original = parse_google_document(raw, title="Report")
    transformed = replace(
        original, blocks=(replace(original.blocks[0], text="New 😀"), original.blocks[1])
    )
    requests = build_copy_mutations(
        original, transformed, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    font_only = [
        request["updateTextStyle"]
        for request in requests
        if request.get("updateTextStyle", {}).get("fields") == "weightedFontFamily.fontFamily"
    ]
    assert font_only[0]["range"] == {"startIndex": 8, "endIndex": 12, "tabId": "tab"}


def test_unchanged_mixed_native_paragraph_keeps_later_style_ranges_absolute() -> None:
    raw = {
        "revisionId": "revision",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab"},
                "documentTab": {
                    "body": {
                        "content": [
                            {
                                "startIndex": 1,
                                "endIndex": 4,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 2,
                                            "textRun": {"content": " "},
                                        },
                                        {
                                            "startIndex": 2,
                                            "endIndex": 3,
                                            "inlineObjectElement": {"inlineObjectId": "logo"},
                                        },
                                        {
                                            "startIndex": 3,
                                            "endIndex": 4,
                                            "textRun": {"content": "\n"},
                                        },
                                    ]
                                },
                            },
                            {
                                "startIndex": 4,
                                "endIndex": 14,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "startIndex": 4,
                                            "endIndex": 13,
                                            "textRun": {
                                                "content": "Purpose😀",
                                                "textStyle": {
                                                    "bold": False,
                                                    "link": {"url": "https://example.test/purpose"},
                                                    "weightedFontFamily": {
                                                        "fontFamily": "Arial",
                                                        "weight": 400,
                                                    },
                                                },
                                            },
                                        },
                                        {
                                            "startIndex": 13,
                                            "endIndex": 14,
                                            "textRun": {
                                                "content": "\n",
                                                "textStyle": {
                                                    "bold": False,
                                                    "weightedFontFamily": {
                                                        "fontFamily": "Arial",
                                                        "weight": 400,
                                                    },
                                                },
                                            },
                                        },
                                    ],
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                },
                            },
                        ]
                    }
                },
            }
        ],
    }
    parsed = parse_google_document(raw, title="Report")

    requests = build_copy_mutations(
        parsed, parsed, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    body_text_styles = [
        request["updateTextStyle"]
        for request in requests
        if "updateTextStyle" in request and "segmentId" not in request["updateTextStyle"]["range"]
    ]

    assert [request["range"] for request in body_text_styles] == [
        {"startIndex": 4, "endIndex": 13, "tabId": "tab"}
    ]
    defended = defend_native_font_requests(requests, document=raw, original=parsed)
    assert [
        request["updateTextStyle"]["range"] for request in defended if "updateTextStyle" in request
    ] == [
        {"startIndex": 4, "endIndex": 13, "tabId": "tab"},
        {"startIndex": 13, "endIndex": 14, "tabId": "tab"},
    ]

    changed = replace(
        parsed,
        blocks=(replace(parsed.blocks[0], text="unsafe"), *parsed.blocks[1:]),
    )
    with pytest.raises(UnsupportedSourceError):
        build_copy_mutations(parsed, changed, profile=SarjBrandProfile(), headers=(), logo_uri="")


def test_font_verification_accepts_coalesced_runs_but_rejects_missing_font() -> None:
    expected = parse_google_document(
        _document([_run("One "), _run("two\n", bold=True)]), title="Report"
    )
    output = _document([_run("One two\n", weightedFontFamily={"fontFamily": "Nunito"})])
    actual = parse_google_document(output, title="Report")
    adjusted = gateway._typography_verification_ir(actual, expected)
    assert not adjusted.blocks[0].editable
    paragraph = output["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]
    assert gateway._font_only_has_brand(paragraph, adjusted.blocks[0])
    paragraph["elements"][0]["textRun"]["textStyle"] = {}
    assert not gateway._font_only_has_brand(paragraph, adjusted.blocks[0])


def test_typography_semantic_exception_is_narrow_and_leaves_checkpoint_hash_unchanged() -> None:
    expected = parse_google_document(_document([_run("One "), _run("two\n")]), title="Report")
    actual = parse_google_document(_document([_run("One two\n")]), title="Report")
    assert expected.semantic_sha256 != actual.semantic_sha256
    assert gateway._typography_content_preserved(actual, expected, verify_brand=True)
    assert not gateway._typography_content_preserved(actual, expected, verify_brand=False)
    changed = replace(actual, blocks=(replace(actual.blocks[0], text="Different"),))
    assert not gateway._typography_content_preserved(changed, expected, verify_brand=True)
    assert not gateway._typography_content_preserved(expected, actual, verify_brand=True)


def test_font_verification_resolves_named_style_and_fails_on_coverage_gap() -> None:
    raw = _document([_run("Linked\n", link={"url": "https://example.com"})])
    parsed = parse_google_document(raw, title="Report")
    tab = raw["tabs"][0]["documentTab"]
    tab["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": {"weightedFontFamily": {"fontFamily": "Nunito"}},
            }
        ]
    }
    paragraph = tab["body"]["content"][0]["paragraph"]
    effective = gateway._effective_paragraph(paragraph, tab)
    assert gateway._font_only_has_brand(effective, parsed.blocks[0])
    effective["elements"][0]["startIndex"] += 1
    assert not gateway._font_only_has_brand(effective, parsed.blocks[0])
