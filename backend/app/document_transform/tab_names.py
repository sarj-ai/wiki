from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Literal, NotRequired, TypedDict
import unicodedata

from app.document_transform.ir import BlockKind, DocumentIR, utf16_length
from app.document_transform.naming import (
    polished_document_name,
    strip_trailing_document_date,
    supported_tab_icon,
    tab_icon_emoji,
)
from app.identifiers import DocsTabId


MAX_TAB_TITLE_UNITS = 60
_MAX_LEAD_IN_WORDS = 12
_GENERIC_DOCUMENT_TITLES = frozenset(
    {"document", "untitled document", "new document", "overview", "introduction"}
)
_GENERIC_TITLE = re.compile(r"(?:tab\s*\d*|untitled(?:\s+tab)?(?:\s*\d*)?)", re.IGNORECASE)


class _TabTitle(TypedDict):
    tabId: DocsTabId
    title: NotRequired[str]
    iconEmoji: NotRequired[str]


class _UpdateTabProperties(TypedDict):
    tabProperties: _TabTitle
    fields: Literal["title", "iconEmoji", "title,iconEmoji"]


class TabRenameRequest(TypedDict):
    updateDocumentTabProperties: _UpdateTabProperties


@dataclass(frozen=True, slots=True)
class NativeTabProperties:
    tab_id: DocsTabId
    title: str
    parent_tab_id: DocsTabId | None = None
    index: int = 0
    icon_emoji: str | None = None


@dataclass(frozen=True, slots=True)
class TabRename:
    tab_id: DocsTabId
    previous_title: str
    title: str
    previous_icon_emoji: str | None = None
    icon_emoji: str | None = None

    def request(self) -> TabRenameRequest:
        properties: _TabTitle = {"tabId": self.tab_id}
        title_changed = self.title != self.previous_title
        icon_changed = self.icon_emoji != self.previous_icon_emoji
        if title_changed:
            properties["title"] = self.title
        if icon_changed:
            if self.icon_emoji is None:
                message = "Tab icon clearing is not a planned enhancement"
                raise ValueError(message)
            properties["iconEmoji"] = self.icon_emoji
        return {
            "updateDocumentTabProperties": {
                "tabProperties": properties,
                "fields": "title,iconEmoji"
                if title_changed and icon_changed
                else "title"
                if title_changed
                else "iconEmoji",
            },
        }


@dataclass(frozen=True, slots=True)
class TabNamingPlan:
    original_tabs: tuple[NativeTabProperties, ...]
    renames: tuple[TabRename, ...]

    def matches_preimage(self, tabs: tuple[NativeTabProperties, ...]) -> bool:
        return tabs == self.original_tabs

    def matches_result(self, tabs: tuple[NativeTabProperties, ...]) -> bool:
        renames = {rename.tab_id: rename for rename in self.renames}
        expected = tuple(
            replace(tab, title=renames[tab.tab_id].title, icon_emoji=renames[tab.tab_id].icon_emoji)
            if tab.tab_id in renames
            else tab
            for tab in self.original_tabs
        )
        return tabs == expected


def plan_tab_names(
    document: DocumentIR,
    tabs: tuple[NativeTabProperties, ...],
    *,
    prefer_document_title: bool = False,
) -> TabNamingPlan:
    if tuple(tab.tab_id for tab in tabs) != document.tab_ids:
        return TabNamingPlan(tabs, ())
    if len({tab.tab_id for tab in tabs}) != len(tabs):
        return TabNamingPlan(tabs, ())
    if any(tab.icon_emoji is not None and not supported_tab_icon(tab.icon_emoji) for tab in tabs):
        return TabNamingPlan(tabs, ())
    occupied: dict[DocsTabId | None, set[str]] = {}
    for tab in tabs:
        occupied.setdefault(tab.parent_tab_id, set()).add(_name_key(tab.title))
    renames: list[TabRename] = []
    for tab in tabs:
        if not _is_generic(tab.title):
            normalized = _truncate(
                polished_document_name(_clean_title(tab.title)), MAX_TAB_TITLE_UNITS
            )
            sibling_conflict = any(
                other.tab_id != tab.tab_id
                and other.parent_tab_id == tab.parent_tab_id
                and _name_key(other.title) == _name_key(normalized)
                for other in tabs
            )
            title = tab.title if not normalized or sibling_conflict else normalized
            icon = _relevant_icon(tab, title)
            if title != tab.title or (icon is not None and icon != tab.icon_emoji):
                renames.append(TabRename(tab.tab_id, tab.title, title, tab.icon_emoji, icon))
            continue
        single_root_tab = len(tabs) == 1 and tab.parent_tab_id is None
        if prefer_document_title:
            candidate = _meaningful_fallback_title(document.title) if single_root_tab else ""
        else:
            candidate = _candidate_title(document, tab.tab_id, single_root_tab=single_root_tab)
        if not candidate:
            candidate = _clean_title(tab.title) or "Tab"
        icon_hint = candidate
        candidate = _truncate(polished_document_name(candidate), MAX_TAB_TITLE_UNITS)
        if not candidate or not any(character.isalpha() for character in candidate):
            if tab.title and utf16_length(tab.title) <= MAX_TAB_TITLE_UNITS:
                icon = _relevant_icon(tab, icon_hint) or tab_icon_emoji(tab.title)
                if icon is not None and icon != tab.icon_emoji:
                    renames.append(
                        TabRename(tab.tab_id, tab.title, tab.title, tab.icon_emoji, icon)
                    )
            continue
        sibling_titles = occupied[tab.parent_tab_id]
        title = candidate if candidate == tab.title else _unique_title(candidate, sibling_titles)
        sibling_titles.add(_name_key(title))
        icon = _relevant_icon(tab, title)
        if title != tab.title or (icon is not None and icon != tab.icon_emoji):
            renames.append(
                TabRename(
                    tab.tab_id,
                    tab.title,
                    title,
                    tab.icon_emoji,
                    icon if icon is not None else tab.icon_emoji,
                )
            )
    return TabNamingPlan(tabs, tuple(renames))


def _relevant_icon(tab: NativeTabProperties, title: str) -> str | None:
    if tab.icon_emoji and tab.icon_emoji != "📄":
        return tab.icon_emoji
    return tab_icon_emoji(title) or tab.icon_emoji


def _candidate_title(document: DocumentIR, tab_id: DocsTabId, *, single_root_tab: bool) -> str:
    heading = _heading_title(document, tab_id)
    if heading:
        return heading
    lead = next(
        (
            block
            for block in document.blocks
            if block.text.strip()
            and block.source_range is not None
            and block.source_range.tab_id == tab_id
            and block.source_range.segment_id is None
        ),
        None,
    )
    lead_title = ""
    if (
        lead is not None
        and lead.editable
        and lead.is_authored_lead_in
        and utf16_length(lead.text) <= MAX_TAB_TITLE_UNITS
        and len(lead.text.split()) <= _MAX_LEAD_IN_WORDS
    ):
        lead_title = _meaningful_fallback_title(lead.text)
    if lead_title:
        return lead_title
    return _meaningful_fallback_title(document.title) if single_root_tab else ""


def _meaningful_fallback_title(text: str) -> str:
    title = _clean_title(text)
    if (
        not title
        or _is_generic(title)
        or title.casefold() in _GENERIC_DOCUMENT_TITLES
        or not any(character.isalpha() for character in title)
        or "://" in title
    ):
        return ""
    return title


def _is_generic(title: str) -> bool:
    cleaned = strip_trailing_document_date(title)
    return not cleaned or _GENERIC_TITLE.fullmatch(cleaned) is not None


def _heading_title(document: DocumentIR, tab_id: DocsTabId) -> str:
    for block in document.blocks:
        if (
            block.kind == BlockKind.HEADING
            and block.level in {0, 1}
            and block.source_range is not None
            and block.source_range.tab_id == tab_id
            and block.source_range.segment_id is None
        ):
            title = _clean_title(block.text)
            if title and not _is_generic(title) and any(char.isalpha() for char in title):
                return title
    return ""


def _clean_title(text: str) -> str:
    normalized = " ".join(text.split())
    # Strip invisible controls, retaining joiners used by real scripts and emoji.
    # Keep the source-grounded topic until the final title-length decision so
    # an unbreakable title can still supply a native icon without being cut.
    return "".join(
        char
        for char in normalized
        if not unicodedata.category(char).startswith("C") or char in {"\u200c", "\u200d"}
    )


def _truncate(text: str, units: int) -> str:
    return truncate_tab_title(text, units)


def truncate_tab_title(text: str, units: int = MAX_TAB_TITLE_UNITS) -> str:
    # Whole whitespace-delimited tokens are a conservative grapheme-safe
    # boundary: do not split combining sequences, flags or ZWJ emoji to fit.
    while utf16_length(text) > units:
        boundary = text.rfind(" ")
        if boundary < 0:
            return ""
        text = text[:boundary].rstrip()
    return text


def _name_key(title: str) -> str:
    return unicodedata.normalize("NFKC", title).strip().casefold()


def _unique_title(title: str, occupied: set[str]) -> str:
    candidate = title
    counter = 1
    while _name_key(candidate) in occupied:
        counter += 1
        suffix = f" ({counter})"
        candidate = _truncate(title, MAX_TAB_TITLE_UNITS - utf16_length(suffix)) + suffix
    return candidate
