from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Literal, TypedDict
import unicodedata

from app.document_transform.ir import BlockKind, DocumentIR, utf16_length
from app.identifiers import DocsTabId


MAX_TAB_TITLE_UNITS = 60
_GENERIC_TITLE = re.compile(r"(?:tab\s*\d*|untitled(?:\s+tab)?(?:\s*\d*)?)", re.IGNORECASE)


class _TabTitle(TypedDict):
    tabId: DocsTabId
    title: str


class _UpdateTabProperties(TypedDict):
    tabProperties: _TabTitle
    fields: Literal["title"]


class TabRenameRequest(TypedDict):
    updateDocumentTabProperties: _UpdateTabProperties


@dataclass(frozen=True, slots=True)
class NativeTabProperties:
    tab_id: DocsTabId
    title: str
    parent_tab_id: DocsTabId | None = None
    index: int = 0


@dataclass(frozen=True, slots=True)
class TabRename:
    tab_id: DocsTabId
    previous_title: str
    title: str

    def request(self) -> TabRenameRequest:
        return {
            "updateDocumentTabProperties": {
                "tabProperties": {"tabId": self.tab_id, "title": self.title},
                "fields": "title",
            },
        }


@dataclass(frozen=True, slots=True)
class TabNamingPlan:
    original_tabs: tuple[NativeTabProperties, ...]
    renames: tuple[TabRename, ...]

    def matches_preimage(self, tabs: tuple[NativeTabProperties, ...]) -> bool:
        return tabs == self.original_tabs

    def matches_result(self, tabs: tuple[NativeTabProperties, ...]) -> bool:
        titles = {rename.tab_id: rename.title for rename in self.renames}
        expected = tuple(
            replace(tab, title=titles.get(tab.tab_id, tab.title)) for tab in self.original_tabs
        )
        return tabs == expected


def plan_tab_names(
    document: DocumentIR,
    tabs: tuple[NativeTabProperties, ...],
) -> TabNamingPlan:
    if tuple(tab.tab_id for tab in tabs) != document.tab_ids:
        return TabNamingPlan(tabs, ())
    if len({tab.tab_id for tab in tabs}) != len(tabs):
        return TabNamingPlan(tabs, ())
    occupied: dict[DocsTabId | None, set[str]] = {}
    for tab in tabs:
        occupied.setdefault(tab.parent_tab_id, set()).add(_name_key(tab.title))
    renames: list[TabRename] = []
    for tab in tabs:
        if not _is_generic(tab.title):
            continue
        candidate = _heading_title(document, tab.tab_id)
        if not candidate:
            continue
        sibling_titles = occupied[tab.parent_tab_id]
        title = _unique_title(candidate, sibling_titles)
        sibling_titles.add(_name_key(title))
        if title != tab.title:
            renames.append(TabRename(tab.tab_id, tab.title, title))
    return TabNamingPlan(tabs, tuple(renames))


def _is_generic(title: str) -> bool:
    cleaned = title.strip()
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
    visible = "".join(
        char
        for char in normalized
        if not unicodedata.category(char).startswith("C") or char in {"\u200c", "\u200d"}
    )
    return _truncate(visible, MAX_TAB_TITLE_UNITS)


def _truncate(text: str, units: int) -> str:
    while utf16_length(text) > units:
        text = text[:-1]
    return text.rstrip()


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
