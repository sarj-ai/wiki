from __future__ import annotations

from pydantic import JsonValue, RootModel, ValidationError
import pytest

from app.document_transform.native_footer import (
    FooterAction,
    FooterCheckpoint,
    FooterCreation,
    NativeFooterPlan,
    NonWritingFooterPlan,
    OwnedFooter,
    WritingFooterPlan,
    plan_native_footer,
)
from app.document_transform.readability_json import obj
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId


LINK = "https://wiki.sarj.ai/s/quarterly"


class _WritePayload(RootModel[WritingFooterPlan]):
    pass


def _tab(text: str = "\n") -> dict[str, JsonValue]:
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


def _plan(
    tab: dict[str, JsonValue],
    *,
    checkpoint: FooterCheckpoint = FooterCreation.NOT_STARTED,
    display_text: str | None = None,
) -> NativeFooterPlan:
    return plan_native_footer(
        tab,
        tab_id=DocsTabId("tab-1"),
        revision_id=DocsRevisionId("rev-1"),
        short_link=LINK,
        checkpoint=checkpoint,
        display_text=display_text,
    )


def test_creation_is_separate_from_population_and_revision_guarded() -> None:
    plan = _plan({"documentStyle": {}})
    assert isinstance(plan, WritingFooterPlan)
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
def test_ambiguous_creation_never_blindly_retries(tab: dict[str, JsonValue]) -> None:
    plan = _plan(tab, checkpoint=FooterCreation.UNCERTAIN)
    assert plan.action == FooterAction.NEEDS_REVIEW
    assert isinstance(plan, NonWritingFooterPlan)


def test_recorded_creation_id_can_be_populated() -> None:
    plan = _plan(_tab(), checkpoint=OwnedFooter(DocsSegmentId("footer-1")))
    assert isinstance(plan, WritingFooterPlan)
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
    plan = _plan(
        _tab(), checkpoint=OwnedFooter(DocsSegmentId("footer-1")), display_text="Open in Wiki"
    )
    assert isinstance(plan, WritingFooterPlan)
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
    plan = _plan(_tab(LINK + "\n"), checkpoint=OwnedFooter(DocsSegmentId("footer-1")))
    assert plan.action == FooterAction.UNCHANGED
    assert isinstance(plan, NonWritingFooterPlan)


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
def test_preserves_nontext_and_suggested_content(element: dict[str, JsonValue]) -> None:
    tab = _tab()
    obj(obj(tab["footers"])["footer-1"])["content"] = [element]
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
def test_incomplete_snapshots_and_pageless_are_preserved(tab: dict[str, JsonValue]) -> None:
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


def test_owned_footer_cannot_authorize_a_different_provider_segment() -> None:
    plan = _plan(_tab(), checkpoint=OwnedFooter(DocsSegmentId("another-footer")))
    assert isinstance(plan, NonWritingFooterPlan)
    assert plan.action is FooterAction.NEEDS_REVIEW


def test_owned_footer_requires_a_provider_identifier() -> None:
    with pytest.raises(ValueError, match="recorded provider identifier"):
        OwnedFooter(DocsSegmentId(""))


@pytest.mark.parametrize("action", ["unchanged", "preserve", "needs_review", "unknown"])
def test_nonwriting_action_cannot_construct_a_write_plan(action: str) -> None:
    with pytest.raises(ValidationError):
        _WritePayload.model_validate(
            {"action": action, "revision_id": "rev", "first_request": {"createFooter": {}}}
        )


def test_writing_plan_requires_a_nonempty_first_command() -> None:
    with pytest.raises(ValidationError):
        WritingFooterPlan(FooterAction.CREATE, DocsRevisionId("rev"), {})
