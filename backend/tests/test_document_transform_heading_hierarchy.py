from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.document_transform import google_gateway
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.google_gateway import verify_brand_application
from app.document_transform.ir import parse_google_document, utf16_length


def _report() -> dict[str, Any]:
    content = []
    index = 1
    for style, text in (
        ("TITLE", "Quarterly service review"),
        ("SUBTITLE", "September 2026 · Internal review"),
        ("HEADING_1", "Reliability"),
        ("NORMAL_TEXT", "The existing service remains available."),
        ("HEADING_2", "Recovery checks"),
        ("NORMAL_TEXT", "Confirm the recorded outcome before retrying."),
        ("HEADING_3", "Evidence"),
        ("NORMAL_TEXT", "Decisions"),
    ):
        end = index + utf16_length(text) + 1
        content.append(
            {
                "startIndex": index,
                "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": style},
                    "elements": [
                        {"startIndex": index, "endIndex": end, "textRun": {"content": text + "\n"}}
                    ],
                },
            }
        )
        index = end
    return {
        "revisionId": "revision",
        "tabs": [
            {"tabProperties": {"tabId": "tab"}, "documentTab": {"body": {"content": content}}}
        ],
    }


def _requests(raw: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = parse_google_document(raw, title="Quarterly review")
    return build_copy_mutations(parsed, parsed, profile=SarjBrandProfile(), headers=(), logo_uri="")


def _apply_styles(raw: dict[str, Any], requests: list[dict[str, Any]]) -> dict[str, Any]:
    result = deepcopy(raw)
    paragraphs = result["tabs"][0]["documentTab"]["body"]["content"]
    for request in requests:
        for method, payload in request.items():
            if method not in {"updateParagraphStyle", "updateTextStyle"}:
                continue
            for element in paragraphs:
                if element["startIndex"] != payload["range"]["startIndex"]:
                    continue
                paragraph = element["paragraph"]
                if method == "updateParagraphStyle":
                    paragraph["paragraphStyle"].update(payload["paragraphStyle"])
                else:
                    paragraph["elements"][0]["textRun"].setdefault("textStyle", {}).update(
                        payload["textStyle"]
                    )
    return result


def test_native_titles_have_distinct_hierarchy_without_promoting_plain_labels() -> None:
    raw = _report()
    before = parse_google_document(raw, title="Quarterly review")
    requests = _requests(raw)
    after_raw = _apply_styles(raw, requests)
    after = parse_google_document(after_raw, title="Quarterly review")
    paragraphs = after_raw["tabs"][0]["documentTab"]["body"]["content"]
    assert before.semantic_sha256 == after.semantic_sha256
    assert [item["paragraph"]["paragraphStyle"]["namedStyleType"] for item in paragraphs] == [
        "TITLE",
        "SUBTITLE",
        "HEADING_1",
        "NORMAL_TEXT",
        "HEADING_2",
        "NORMAL_TEXT",
        "HEADING_3",
        "NORMAL_TEXT",
    ]
    sizes = [
        item["paragraph"]["elements"][0]["textRun"]["textStyle"]["fontSize"]["magnitude"]
        for item in paragraphs
    ]
    assert sizes[0] > sizes[2] > sizes[4] > sizes[6] >= sizes[3]
    assert sizes[1] < sizes[2]
    assert paragraphs[1]["paragraph"]["elements"][0]["textRun"]["textStyle"]["bold"] is False
    assert (
        paragraphs[2]["paragraph"]["paragraphStyle"]["spaceAbove"]["magnitude"]
        > paragraphs[3]["paragraph"]["paragraphStyle"]["spaceAbove"]["magnitude"]
    )
    assert _requests(after_raw) == requests
    assert _apply_styles(after_raw, requests) == after_raw


def test_losing_title_semantics_changes_content_fingerprint() -> None:
    raw = _report()
    before = parse_google_document(raw, title="Quarterly review")
    raw["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["paragraphStyle"][
        "namedStyleType"
    ] = "HEADING_1"
    assert (
        parse_google_document(raw, title="Quarterly review").semantic_sha256
        != before.semantic_sha256
    )


def test_linked_title_remains_font_only_and_keeps_its_native_style() -> None:
    raw = _report()
    first = raw["tabs"][0]["documentTab"]["body"]["content"][0]
    first["paragraph"]["elements"][0]["textRun"]["textStyle"] = {
        "link": {"url": "https://example.com/report"},
        "bold": True,
    }
    requests = _requests(raw)
    targeting_first = [
        payload
        for request in requests
        for payload in request.values()
        if payload.get("range", {}).get("startIndex") == 1
    ]
    assert len(targeting_first) == 1
    assert targeting_first[0]["fields"] == "weightedFontFamily.fontFamily"
    assert (
        parse_google_document(raw, title="Quarterly review").blocks[0].native_named_style == "TITLE"
    )


def test_typography_alone_does_not_count_as_fully_branded() -> None:
    # Shell verification is exercised elsewhere; this checks a missing shell
    # never becomes sufficient merely because typography was applied.
    raw = _apply_styles(_report(), _requests(_report()))
    assert not verify_brand_application(raw, parse_google_document(raw, title="Quarterly review"))


def test_title_and_subtitle_readback_enforces_actual_native_styles() -> None:
    raw = _apply_styles(_report(), _requests(_report()))
    parsed = parse_google_document(raw, title="Quarterly review")
    paragraphs = raw["tabs"][0]["documentTab"]["body"]["content"]
    for element, block in zip(paragraphs, parsed.blocks, strict=True):
        assert all(
            google_gateway._paragraph_brand_diagnostics(element["paragraph"], block).values()
        )
    title = paragraphs[0]["paragraph"]
    title["paragraphStyle"]["namedStyleType"] = "HEADING_1"
    assert not google_gateway._paragraph_brand_diagnostics(title, parsed.blocks[0])["named_style"]
    subtitle = paragraphs[1]["paragraph"]
    subtitle["elements"][0]["textRun"]["textStyle"]["bold"] = True
    assert not google_gateway._paragraph_brand_diagnostics(subtitle, parsed.blocks[1])["text_style"]


def test_native_numbering_is_not_recreated_or_renumbered() -> None:
    raw = _report()
    paragraph = raw["tabs"][0]["documentTab"]["body"]["content"][5]["paragraph"]
    paragraph["bullet"] = {"listId": "authored-list", "nestingLevel": 1}
    before = deepcopy(paragraph["bullet"])
    requests = _requests(raw)
    assert all("createParagraphBullets" not in request for request in requests)
    assert all("deleteParagraphBullets" not in request for request in requests)
    result = _apply_styles(raw, requests)
    assert result["tabs"][0]["documentTab"]["body"]["content"][5]["paragraph"]["bullet"] == before
