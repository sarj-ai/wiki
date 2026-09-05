# Persist the create response's footerId, reread, then plan population.
# After an ambiguous response, prove ownership before planning further writes.

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypedDict
from urllib.parse import urlsplit

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


@dataclass(frozen=True, slots=True)
class FooterCheckpoint:
    # Ownership cannot be inferred from an arbitrary existing footer.
    creation_attempted: bool = False
    owned_footer_id: str | None = None


class FooterWriteControl(TypedDict):
    requiredRevisionId: DocsRevisionId


class FooterBatchBody(TypedDict):
    requests: list[DocsRequest]
    writeControl: FooterWriteControl


@dataclass(frozen=True, slots=True)
class NativeFooterPlan:
    action: FooterAction
    revision_id: DocsRevisionId
    requests: tuple[DocsRequest, ...] = ()

    def batch_body(self) -> FooterBatchBody:
        # CAS also protects empty-footer plans from concurrent custom edits.
        if not self.requests:
            msg = "A non-writing footer plan cannot be submitted"
            raise ValueError(msg)
        return {
            "requests": list(self.requests),
            "writeControl": {"requiredRevisionId": self.revision_id},
        }


def plan_native_footer(
    document_tab: dict[str, Any],
    *,
    tab_id: DocsTabId,
    revision_id: DocsRevisionId,
    short_link: str,
    display_text: str | None = None,
    checkpoint: FooterCheckpoint | None = None,
) -> NativeFooterPlan:
    # Supply the complete documentTab, including footers, and an issued URL.
    # Persist the checkpoint before dispatching batch_body(); recover a lost
    # population reply by reading the exact content, never replacing custom text.
    _validate_inputs(tab_id=tab_id, revision_id=revision_id, short_link=short_link)
    if display_text not in {None, FOOTER_LINK_CAPTION}:
        msg = "Footer caption must use the pinned Wiki label"
        raise ValueError(msg)
    caption = display_text if display_text is not None else short_link
    checkpoint = checkpoint or FooterCheckpoint()
    style = document_tab.get("documentStyle")
    if not isinstance(style, dict):
        return NativeFooterPlan(FooterAction.PRESERVE, revision_id)
    document_format = style.get("documentFormat", {})
    if not isinstance(document_format, dict) or document_format.get("documentMode") == "PAGELESS":
        return NativeFooterPlan(FooterAction.PRESERVE, revision_id)
    footer_id = style.get("defaultFooterId")
    if not footer_id:
        if checkpoint.creation_attempted:
            return NativeFooterPlan(FooterAction.NEEDS_REVIEW, revision_id)
        return NativeFooterPlan(
            FooterAction.CREATE,
            revision_id,
            (
                {
                    "createFooter": {
                        "type": "DEFAULT",
                        "sectionBreakLocation": {"index": 0, "tabId": tab_id},
                    }
                },
            ),
        )
    if not isinstance(footer_id, str):
        return NativeFooterPlan(FooterAction.NEEDS_REVIEW, revision_id)
    if checkpoint.creation_attempted and checkpoint.owned_footer_id != footer_id:
        return NativeFooterPlan(FooterAction.NEEDS_REVIEW, revision_id)
    footers = document_tab.get("footers", {})
    footer = footers.get(footer_id) if isinstance(footers, dict) else None
    text = _plain_footer_text(footer)
    if text == caption + "\n":
        return NativeFooterPlan(FooterAction.UNCHANGED, revision_id)
    if text != "\n":
        return NativeFooterPlan(FooterAction.PRESERVE, revision_id)
    return NativeFooterPlan(
        FooterAction.POPULATE,
        revision_id,
        _population_requests(
            tab_id=tab_id,
            footer_id=DocsSegmentId(footer_id),
            short_link=short_link,
            caption=caption,
        ),
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
