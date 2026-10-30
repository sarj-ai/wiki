from __future__ import annotations

from typing import Any

import pytest

from app.document_transform.native_footer import (
    FooterAction,
    FooterCheckpoint,
    plan_native_footer,
)
from app.identifiers import DocsRevisionId, DocsTabId


LINK = "https://wiki.sarj.ai/s/quarterly"


def _tab(text: str = "\n") -> dict[str, Any]:
    return {
        "documentStyle": {"defaultFooterId": "footer-1"},
        "footers": {
            "footer-1": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": text}}],
                        }
                    }
                ]
            }
        },
    }


def _plan(tab: dict[str, Any], **kwargs: Any) -> Any:
    return plan_native_footer(
        tab,
        tab_id=DocsTabId("tab-1"),
        revision_id=DocsRevisionId("rev-1"),
        short_link=LINK,
        **kwargs,
    )


def test_creation_is_separate_from_population_and_revision_guarded() -> None:
    plan = _plan({"documentStyle": {}})
    assert plan.action == FooterAction.CREATE
    assert plan.batch_body() == {
        "requests": [
            {
                "createFooter": {
                    "type": "DEFAULT",
                    "sectionBreakLocation": {"index": 0, "tabId": "tab-1"},
                }
            }
        ],
        "writeControl": {"requiredRevisionId": "rev-1"},
    }


@pytest.mark.parametrize("tab", [{"documentStyle": {}}, _tab()], ids=["absent", "empty"])
def test_ambiguous_creation_never_blindly_retries(tab: dict[str, Any]) -> None:
    plan = _plan(tab, checkpoint=FooterCheckpoint(creation_attempted=True))
    assert plan.action == FooterAction.NEEDS_REVIEW
    assert not plan.requests


def test_recorded_creation_id_can_be_populated() -> None:
    plan = _plan(_tab(), checkpoint=FooterCheckpoint(True, "footer-1"))
    assert plan.action == FooterAction.POPULATE
    assert plan.requests[0]["insertText"]["location"] == {
        "index": 0,
        "segmentId": "footer-1",
        "tabId": "tab-1",
    }
    assert "updateParagraphStyle" in plan.requests[1]
    assert plan.requests[2]["updateTextStyle"]["textStyle"]["weightedFontFamily"] == {
        "fontFamily": "Nunito",
    }
    assert plan.requests[2]["updateTextStyle"]["textStyle"]["link"] == {"url": LINK}


def test_caption_population_keeps_exact_destination_and_caption_sized_ranges() -> None:
    plan = _plan(_tab(), checkpoint=FooterCheckpoint(True, "footer-1"), display_text="Open in Wiki")
    assert plan.requests[0]["insertText"]["text"] == "Open in Wiki"
    assert plan.requests[2]["updateTextStyle"]["textStyle"]["link"] == {"url": LINK}
    assert plan.requests[2]["updateTextStyle"]["range"]["endIndex"] == len("Open in Wiki")
    assert (
        _plan(_tab("Open in Wiki\n"), display_text="Open in Wiki").action == FooterAction.UNCHANGED
    )


def test_unknown_caption_is_rejected_and_legacy_footer_is_not_rewritten() -> None:
    with pytest.raises(ValueError, match="pinned Wiki label"):
        _plan(_tab(), display_text="Invented title")
    assert _plan(_tab(LINK + "\n"), display_text="Open in Wiki").action == FooterAction.PRESERVE


def test_repeat_and_lost_population_reply_do_not_duplicate() -> None:
    plan = _plan(_tab(LINK + "\n"), checkpoint=FooterCheckpoint(True, "footer-1"))
    assert plan.action == FooterAction.UNCHANGED
    assert not plan.requests
    with pytest.raises(ValueError, match="non-writing"):
        plan.batch_body()


@pytest.mark.parametrize("text", ["Confidential\n", " \n", "old link\n", LINK + "\n\n"])
def test_preserves_custom_text_and_whitespace(text: str) -> None:
    assert _plan(_tab(text)).action == FooterAction.PRESERVE


@pytest.mark.parametrize(
    "element",
    [
        {"table": {"tableRows": []}},
        {"paragraph": {"elements": [{"inlineObjectElement": {"inlineObjectId": "logo"}}]}},
        {
            "paragraph": {
                "positionedObjectIds": ["logo"],
                "elements": [{"textRun": {"content": "\n"}}],
            }
        },
        {
            "paragraph": {
                "elements": [{"textRun": {"content": "\n"}, "suggestedInsertionIds": ["x"]}]
            }
        },
    ],
    ids=["table", "inline-image", "positioned-image", "suggestion"],
)
def test_preserves_nontext_and_suggested_content(element: dict[str, Any]) -> None:
    tab = _tab()
    tab["footers"]["footer-1"]["content"] = [element]
    assert _plan(tab).action == FooterAction.PRESERVE


@pytest.mark.parametrize(
    "tab",
    [
        {},
        {"documentStyle": {"documentFormat": {"documentMode": "PAGELESS"}}},
        {"documentStyle": {"defaultFooterId": "missing"}},
    ],
    ids=["incomplete", "pageless", "missing-footer"],
)
def test_incomplete_snapshots_and_pageless_are_preserved(tab: dict[str, Any]) -> None:
    assert _plan(tab).action == FooterAction.PRESERVE


@pytest.mark.parametrize(
    "link",
    [
        "http://example.com/a",
        "javascript:x",
        "https://u:p@x/a",
        "https://x/a\nb",
        "https://x/" + "a" * 201,
    ],
)
def test_rejects_unsafe_or_noncompact_link(link: str) -> None:
    with pytest.raises(ValueError, match="issued compact HTTPS"):
        plan_native_footer(
            _tab(), tab_id=DocsTabId("tab"), revision_id=DocsRevisionId("rev"), short_link=link
        )
