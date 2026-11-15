# Persist the create response's footerId, reread, then plan population.
# After an ambiguous response, prove ownership before planning further writes.

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypedDict, assert_never
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from app.document_transform.brand import DocsRequest, SarjBrandProfile
from app.document_transform.ir import utf16_length
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId


MAX_FOOTER_URL_LENGTH = 200
FIRST_PRINTABLE_CODEPOINT = 32
FOOTER_LINK_CAPTION = "Open in Wiki"


class FooterAction(StrEnum):
    CREATE = "create"
    POPULATE = "populate"
    UNCHANGED = "unchanged"
    PRESERVE = "preserve"
    NEEDS_REVIEW = "needs_review"


class FooterCreation(StrEnum):
    NOT_STARTED = "not_started"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class OwnedFooter:
    footer_id: DocsSegmentId

    def __post_init__(self) -> None:
        if not self.footer_id:
            msg = "An owned footer requires its recorded provider identifier"
            raise ValueError(msg)


type FooterCheckpoint = FooterCreation | OwnedFooter


class FooterWriteControl(TypedDict):
    requiredRevisionId: DocsRevisionId


class FooterBatchBody(TypedDict):
    requests: list[DocsRequest]
    writeControl: FooterWriteControl


@dataclass(frozen=True, slots=True, config=ConfigDict(extra="forbid"))
class WritingFooterPlan:
    action: Literal[FooterAction.CREATE, FooterAction.POPULATE]
    revision_id: DocsRevisionId
    first_request: DocsRequest = Field(min_length=1)
    remaining_requests: tuple[DocsRequest, ...] = ()

    @property
    def requests(self) -> tuple[DocsRequest, *tuple[DocsRequest, ...]]:
        return self.first_request, *self.remaining_requests

    def batch_body(self) -> FooterBatchBody:
        # CAS also protects empty-footer plans from concurrent custom edits.
        return {
            "requests": list(self.requests),
            "writeControl": {"requiredRevisionId": self.revision_id},
        }


@dataclass(frozen=True, slots=True, config=ConfigDict(extra="forbid"))
class NonWritingFooterPlan:
    action: Literal[FooterAction.UNCHANGED, FooterAction.PRESERVE, FooterAction.NEEDS_REVIEW]
    revision_id: DocsRevisionId


type NativeFooterPlan = WritingFooterPlan | NonWritingFooterPlan


def plan_native_footer(
    document_tab: dict[str, Any],
    *,
    tab_id: DocsTabId,
    revision_id: DocsRevisionId,
    short_link: str,
    display_text: str | None = None,
    checkpoint: FooterCheckpoint = FooterCreation.NOT_STARTED,
) -> NativeFooterPlan:
    # Supply the complete documentTab, including footers, and an issued URL.
    # Persist the checkpoint before dispatching batch_body(); recover a lost
    # population reply by reading the exact content, never replacing custom text.
    _validate_inputs(tab_id=tab_id, revision_id=revision_id, short_link=short_link)
    if display_text not in {None, FOOTER_LINK_CAPTION}:
        msg = "Footer caption must use the pinned Wiki label"
        raise ValueError(msg)
    caption = display_text if display_text is not None else short_link
    style = document_tab.get("documentStyle")
    if not isinstance(style, dict):
        return NonWritingFooterPlan(FooterAction.PRESERVE, revision_id)
    document_format = style.get("documentFormat", {})
    if not isinstance(document_format, dict) or document_format.get("documentMode") == "PAGELESS":
        return NonWritingFooterPlan(FooterAction.PRESERVE, revision_id)
    footer_id = style.get("defaultFooterId")
    if not footer_id:
        if checkpoint is not FooterCreation.NOT_STARTED:
            return NonWritingFooterPlan(FooterAction.NEEDS_REVIEW, revision_id)
        return WritingFooterPlan(
            FooterAction.CREATE,
            revision_id,
            {
                "createFooter": {
                    "type": "DEFAULT",
                    "sectionBreakLocation": {"index": 0, "tabId": tab_id},
                }
            },
        )
    if not isinstance(footer_id, str):
        return NonWritingFooterPlan(FooterAction.NEEDS_REVIEW, revision_id)
    match checkpoint:
        case FooterCreation.UNCERTAIN:
            return NonWritingFooterPlan(FooterAction.NEEDS_REVIEW, revision_id)
        case OwnedFooter(footer_id=owned_id) if owned_id != footer_id:
            return NonWritingFooterPlan(FooterAction.NEEDS_REVIEW, revision_id)
        case FooterCreation.NOT_STARTED | OwnedFooter():
            pass
        case _:
            assert_never(checkpoint)
    footers = document_tab.get("footers", {})
    footer = footers.get(footer_id) if isinstance(footers, dict) else None
    text = _plain_footer_text(footer)
    if text == caption + "\n":
        return NonWritingFooterPlan(FooterAction.UNCHANGED, revision_id)
    if text != "\n":
        return NonWritingFooterPlan(FooterAction.PRESERVE, revision_id)
    first, *remaining = _population_requests(
        tab_id=tab_id,
        footer_id=DocsSegmentId(footer_id),
        short_link=short_link,
        caption=caption,
    )
    return WritingFooterPlan(
        FooterAction.POPULATE,
        revision_id,
        first,
        tuple(remaining),
    )


def _validate_inputs(*, tab_id: DocsTabId, revision_id: DocsRevisionId, short_link: str) -> None:
    parsed = urlsplit(short_link)
    valid_location = bool(tab_id and revision_id)
    valid_url = bool(
        parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
    )
    valid_text = len(short_link) <= MAX_FOOTER_URL_LENGTH and not any(
        character.isspace() or ord(character) < FIRST_PRINTABLE_CODEPOINT
        for character in short_link
    )
    if not valid_location or not valid_url or not valid_text:
        msg = "Footer requires a revision, tab, and issued compact HTTPS URL"
        raise ValueError(msg)


def _plain_footer_text(footer: object) -> str | None:
    # Only a single plain paragraph is eligible; preserve all unknown objects.
    if not isinstance(footer, dict):
        return None
    content = footer.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    element = content[0]
    if not isinstance(element, dict) or set(element) - {"startIndex", "endIndex", "paragraph"}:
        return None
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict) or set(paragraph) - {"elements", "paragraphStyle"}:
        return None
    elements = paragraph.get("elements")
    if not isinstance(elements, list) or not elements:
        return None
    parts: list[str] = []
    for run in elements:
        if not isinstance(run, dict) or set(run) - {"startIndex", "endIndex", "textRun"}:
            return None
        text_run = run.get("textRun")
        if not isinstance(text_run, dict) or set(text_run) - {"content", "textStyle"}:
            return None
        text = text_run.get("content")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "".join(parts)


def _population_requests(
    *, tab_id: DocsTabId, footer_id: DocsSegmentId, short_link: str, caption: str
) -> tuple[DocsRequest, ...]:
    profile = SarjBrandProfile()
    range_value = {
        "tabId": tab_id,
        "segmentId": footer_id,
        "startIndex": 0,
        "endIndex": utf16_length(caption),
    }
    return (
        {
            "insertText": {
                "location": {"tabId": tab_id, "segmentId": footer_id, "index": 0},
                "text": caption,
            }
        },
        {
            "updateParagraphStyle": {
                "range": range_value,
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    "alignment": "END",
                    "spaceAbove": {"magnitude": 0, "unit": "PT"},
                    "spaceBelow": {"magnitude": 0, "unit": "PT"},
                },
                "fields": "namedStyleType,alignment,spaceAbove,spaceBelow",
            }
        },
        {
            "updateTextStyle": {
                "range": range_value,
                "textStyle": {
                    "weightedFontFamily": {"fontFamily": profile.font_family},
                    "fontSize": {"magnitude": profile.caption_size_pt, "unit": "PT"},
                    "bold": False,
                    "italic": False,
                    "link": {"url": short_link},
                },
                "fields": "weightedFontFamily,fontSize,bold,italic,link",
            }
        },
    )
