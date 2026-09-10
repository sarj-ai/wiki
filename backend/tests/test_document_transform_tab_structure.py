from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from app.document_transform.ir import utf16_length
from app.document_transform.tab_structure import can_retry_tab_creation, plan_tab_structure


def _document() -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"endIndex": 1, "sectionBreak": {"sectionStyle": {}}}]
    index = 1
    for title in ("Priorities", "Delivery", "Risks"):
        for text, style in (
            (title + "\n", "HEADING_1"),
            ("Reliable 🌍 work. " * 200 + "\n", "NORMAL_TEXT"),
        ):
            end = index + utf16_length(text)
            content.append(
                {
                    "startIndex": index,
                    "endIndex": end,
                    "paragraph": {
                        "paragraphStyle": {"namedStyleType": style},
                        "elements": [
                            {
                                "startIndex": index,
                                "endIndex": end,
                                "textRun": {
                                    "content": text,
                                    "textStyle": {"bold": style == "HEADING_1"},
                                },
                            }
                        ],
                    },
                }
            )
            index = end
    return {
        "revisionId": "rev1",
        "tabs": [
            {"tabProperties": {"tabId": "tab1"}, "documentTab": {"body": {"content": content}}}
        ],
    }


def test_sections_are_deterministic_and_preserve_exact_utf16_text_and_styles():
    document = _document()
    before = deepcopy(document)
    plan = plan_tab_structure(document, comments_verified_absent=True)
    assert plan is not None
    assert plan == plan_tab_structure(document, comments_verified_absent=True)
    assert [section.title for section in plan.sections] == [
        "Priorities",
        "Delivery",
        "Risks",
    ]
    assert plan.preserves_inventory(plan.sections)
    assert not plan.preserves_inventory(tuple(reversed(plan.sections)))
    assert document == before
    assert sum(utf16_length(s.text) for s in plan.sections) == plan.sections[-1].end_index - 1
    altered = replace(plan.sections[0], paragraphs=("changed",))
    assert not plan.preserves_inventory((altered, *plan.sections[1:]))


@pytest.mark.parametrize(
    "feature", ["headers", "footers", "inlineObjects", "namedRanges", "footnotes"]
)
def test_protected_native_features_decline(feature: str):
    document = _document()
    document["tabs"][0]["documentTab"][feature] = {"protected": {}}
    assert plan_tab_structure(document, comments_verified_absent=True) is None


@pytest.mark.parametrize("custom_margins", [False, True], ids=["default", "custom"])
def test_header_footer_layout_settings_do_not_masquerade_as_occupied_segments(custom_margins: bool):
    document = _document()
    document["tabs"][0]["documentTab"]["documentStyle"] = {
        "marginHeader": {"magnitude": 36, "unit": "PT"},
        "marginFooter": {"magnitude": 36, "unit": "PT"},
        "useFirstPageHeaderFooter": False,
        "useEvenPageHeaderFooter": False,
        "useCustomHeaderFooterMargins": custom_margins,
    }
    before = deepcopy(document)
    assert plan_tab_structure(document, comments_verified_absent=True) is not None
    assert document == before


@pytest.mark.parametrize(
    "value", [None, 0, 1, "false", {}, []], ids=["null", "zero", "one", "string", "object", "array"]
)
def test_custom_header_footer_layout_flag_rejects_malformed_values(value: object):
    document = _document()
    document["tabs"][0]["documentTab"]["documentStyle"] = {"useCustomHeaderFooterMargins": value}
    assert plan_tab_structure(document, comments_verified_absent=True) is None


@pytest.mark.parametrize(
    "field", ["defaultHeaderId", "firstPageFooterId", "evenPageHeaderId", "futureHeaderProperty"]
)
def test_segment_references_and_unknown_header_fields_still_decline(field: str):
    document = _document()
    document["tabs"][0]["documentTab"]["documentStyle"] = {field: "protected"}
    assert plan_tab_structure(document, comments_verified_absent=True) is None


def test_comments_unknown_existing_tabs_and_short_documents_decline():
    document = _document()
    assert plan_tab_structure(document) is None
    document["tabs"].append(deepcopy(document["tabs"][0]))
    assert plan_tab_structure(document, comments_verified_absent=True) is None
    document = _document()
    document["tabs"][0]["documentTab"]["body"]["content"] = []
    assert plan_tab_structure(document, comments_verified_absent=True) is None


@pytest.mark.parametrize("feature", ["link", "suggestion", "object"])
def test_links_suggestions_and_nontext_decline(feature: str):
    document = _document()
    run = document["tabs"][0]["documentTab"]["body"]["content"][1]["paragraph"]["elements"][0]
    match feature:
        case "link":
            run["textRun"]["textStyle"]["link"] = {"url": "https://example.test"}
        case "suggestion":
            run["suggestedInsertionIds"] = ["suggestion"]
        case _:
            run["inlineObjectElement"] = {"inlineObjectId": "image"}
    assert plan_tab_structure(document, comments_verified_absent=True) is None


def test_native_lists_are_retained_in_inventory():
    document = _document()
    native = document["tabs"][0]["documentTab"]
    native["lists"] = {"list1": {"listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}]}}}
    native["body"]["content"][2]["paragraph"]["bullet"] = {"listId": "list1", "nestingLevel": 0}
    plan = plan_tab_structure(document, comments_verified_absent=True)
    assert plan is not None
    assert '"glyphType":"DECIMAL"' in plan.lists_json
    assert '"listId":"list1"' in plan.sections[0].paragraphs[1]


def test_creation_can_only_begin_before_durable_dispatch_intent():
    assert can_retry_tab_creation(dispatch_started=False, persisted_tab_id=None)
    assert not can_retry_tab_creation(dispatch_started=True, persisted_tab_id=None)
    assert not can_retry_tab_creation(dispatch_started=True, persisted_tab_id="created")
    assert not can_retry_tab_creation(dispatch_started=False, persisted_tab_id="created")
