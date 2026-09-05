from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from itertools import pairwise
import json
from typing import Any

from app.document_transform.ir import utf16_length
from app.document_transform.naming import (
    supported_tab_icon,
    tab_icon_emoji,
    title_case_document_name,
)
from app.document_transform.tab_names import MAX_TAB_TITLE_UNITS, truncate_tab_title
from app.identifiers import DocsRevisionId, DocsTabId


MIN_DOCUMENT_WORDS = 1500
MIN_SECTION_WORDS = 200
MIN_SECTIONS = 3
MAX_SECTIONS = 6
_PERMITTED_NATIVE_FIELDS = frozenset({"body", "documentStyle", "namedStyles", "lists"})
HEADER_FOOTER_LAYOUT_FIELDS = frozenset(
    {
        "marginHeader",
        "marginFooter",
        "useFirstPageHeaderFooter",
        "useEvenPageHeaderFooter",
        "useCustomHeaderFooterMargins",
    }
)


@dataclass(frozen=True, slots=True)
class TabSection:
    operation_id: str
    title: str
    start_index: int
    end_index: int
    # Canonical snapshots preserve native runs, styles, bullets and list definitions.
    paragraphs: tuple[str, ...]
    text: str
    icon_emoji: str | None = None


@dataclass(frozen=True, slots=True)
class TabStructurePlan:
    source_tab_id: DocsTabId
    revision_id: DocsRevisionId
    sections: tuple[TabSection, ...]
    lists_json: str
    inventory_sha256: str
    section_break_json: str = "{}"
    source_tab_properties_json: str = "{}"

    def preserves_inventory(self, sections: tuple[TabSection, ...]) -> bool:
        # Exact ordered snapshots, not a normalized word count or semantic hash.
        return (
            tuple(section.operation_id for section in sections)
            == tuple(section.operation_id for section in self.sections)
            and _inventory(sections, self.lists_json) == self.inventory_sha256
        )


def plan_tab_structure(
    document: dict[str, Any], *, comments_verified_absent: bool = False
) -> TabStructurePlan | None:
    # Docs does not expose Drive comments in its body response. The caller must
    # verify their absence separately; unknown permission is not absence.
    if (
        not comments_verified_absent
        or not isinstance(document.get("revisionId"), str)
        or not document["revisionId"]
    ):
        return None
    tabs = document.get("tabs", [])
    if (
        not isinstance(tabs, list)
        or len(tabs) != 1
        or not isinstance(tabs[0], dict)
        or tabs[0].get("childTabs")
    ):
        return None
    tab = tabs[0]
    properties = tab.get("tabProperties", {})
    if not isinstance(properties, dict) or set(properties) - {
        "tabId",
        "title",
        "parentTabId",
        "index",
        "nestingLevel",
        "iconEmoji",
    }:
        return None
    if "iconEmoji" in properties and not supported_tab_icon(properties["iconEmoji"]):
        return None
    tab_id = properties.get("tabId")
    native = tab.get("documentTab", {})
    if not isinstance(tab_id, str) or not tab_id or not _safe_native(native):
        return None
    paragraphs = _paragraphs(native.get("body", {}).get("content", []))
    if not paragraphs or sum(len(_text(p).split()) for p in paragraphs) < MIN_DOCUMENT_WORDS:
        return None
    starts = _section_starts(paragraphs)
    if not starts:
        return None
    sections = _sections(paragraphs, starts, tab_id, document["revisionId"])
    if properties.get("iconEmoji") or (
        "iconEmoji" in properties and sections[0].icon_emoji is None
    ):
        sections = (replace(sections[0], icon_emoji=properties["iconEmoji"]), *sections[1:])
    lists_json = _canonical(native.get("lists", {}))
    return TabStructurePlan(
        DocsTabId(tab_id),
        DocsRevisionId(document["revisionId"]),
        sections,
        lists_json,
        _inventory(sections, lists_json),
        _canonical(native["body"]["content"][0]),
        _canonical(properties),
    )


def can_retry_tab_creation(*, dispatch_started: bool, persisted_tab_id: str | None) -> bool:
    # Dispatch intent must be committed before the provider call. A known ID
    # means resume that tab, not create again; timeouts can follow success.
    return not dispatch_started and persisted_tab_id is None


def _safe_native(native: object) -> bool:
    native = _mapping(native)
    if native is None:
        return False
    # Missing future provider fields must not silently become dropped objects.
    if any(key not in _PERMITTED_NATIVE_FIELDS and value for key, value in native.items()):
        return False
    style = native.get("documentStyle", {})
    if not isinstance(style, dict):
        return False
    if not isinstance(native.get("body"), dict) or not isinstance(native.get("lists", {}), dict):
        return False
    if "useCustomHeaderFooterMargins" in style and not isinstance(
        style["useCustomHeaderFooterMargins"], bool
    ):
        return False
    # Layout settings do not imply an occupied segment. Native header/footer
    # maps are already rejected above; retain fail-closed handling for IDs and
    # unknown future header/footer properties. Execution still compares the
    # complete unmodified documentStyle across every created tab.
    return not any(
        ("header" in key.lower() or "footer" in key.lower())
        and key not in HEADER_FOOTER_LAYOUT_FIELDS
        for key in style
    )


def _paragraphs(content: object) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    output: list[dict[str, Any]] = []
    expected = 1
    for position, raw_element in enumerate(content):
        element = _mapping(raw_element)
        if element is None:
            return []
        if element.get("startIndex", 0) == 0 and element.get("endIndex") == 1:
            if (
                position != 0
                or set(element) - {"startIndex", "endIndex", "sectionBreak"}
                or not isinstance(element.get("sectionBreak"), dict)
            ):
                return []
            continue
        if position == 0:
            return []
        paragraph = element.get("paragraph")
        if not isinstance(paragraph, dict) or element.get("startIndex") != expected:
            return []
        if set(element) - {"startIndex", "endIndex", "paragraph"}:
            return []
        if set(paragraph) - {"elements", "paragraphStyle", "bullet"}:
            return []
        if not isinstance(paragraph.get("paragraphStyle", {}), dict):
            return []
        if not _safe_runs(paragraph.get("elements", [])):
            return []
        text = _text(element)
        if not text.endswith("\n") or element.get("endIndex") != expected + utf16_length(text):
            return []
        expected += utf16_length(text)
        output.append(element)
    return output


def _mapping(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        return None
    return {str(key): child for key, child in value.items()}


def _safe_runs(runs: object) -> bool:
    if not isinstance(runs, list) or not runs:
        return False
    for run in runs:
        if not isinstance(run, dict) or set(run) - {"startIndex", "endIndex", "textRun"}:
            return False
        text_run = run.get("textRun")
        if not isinstance(text_run, dict) or set(text_run) - {"content", "textStyle"}:
            return False
        style = text_run.get("textStyle", {})
        if not isinstance(style, dict) or "link" in style:
            return False
        if not isinstance(text_run.get("content"), str):
            return False
    return True


def _text(paragraph: dict[str, Any]) -> str:
    return "".join(run["textRun"]["content"] for run in paragraph["paragraph"]["elements"])


def _section_starts(paragraphs: list[dict[str, Any]]) -> list[int]:
    for level in ("HEADING_1", "HEADING_2"):
        headings = [
            index
            for index, paragraph in enumerate(paragraphs)
            if paragraph["paragraph"].get("paragraphStyle", {}).get("namedStyleType") == level
        ]
        if not MIN_SECTIONS <= len(headings) <= MAX_SECTIONS:
            continue
        starts = ([0] if headings[0] else []) + headings
        boundaries = [*starts, len(paragraphs)]
        # An introduction is kept with the first section if it would be a tiny tab.
        if (
            headings[0]
            and sum(len(_text(p).split()) for p in paragraphs[: headings[0]]) < MIN_SECTION_WORDS
        ):
            starts = [0, *headings[1:]]
            boundaries = [*starts, len(paragraphs)]
        if all(
            sum(len(_text(p).split()) for p in paragraphs[start:end]) >= MIN_SECTION_WORDS
            for start, end in pairwise(boundaries)
        ):
            return starts
    return []


def _sections(
    paragraphs: list[dict[str, Any]], starts: list[int], tab_id: str, revision: str
) -> tuple[TabSection, ...]:
    output: list[TabSection] = []
    occupied: set[str] = set()
    for start, end in zip(starts, [*starts[1:], len(paragraphs)], strict=True):
        selected = paragraphs[start:end]
        heading = next(
            (
                _text(p).strip()
                for p in selected
                if p["paragraph"].get("paragraphStyle", {}).get("namedStyleType")
                in {"HEADING_1", "HEADING_2"}
            ),
            "Overview",
        )
        title = truncate_tab_title(
            title_case_document_name(" ".join(heading.split()) or "Overview")
        )
        if not any(character.isalpha() for character in title):
            title = "Overview"
        base = title
        suffix = 1
        while title.casefold() in occupied:
            suffix += 1
            label = f" ({suffix})"
            title = truncate_tab_title(base, MAX_TAB_TITLE_UNITS - utf16_length(label)) + label
        occupied.add(title.casefold())
        snapshots = tuple(_canonical(p) for p in selected)
        identity = _canonical([tab_id, revision, snapshots])
        output.append(
            TabSection(
                hashlib.sha256(identity.encode()).hexdigest(),
                title,
                selected[0]["startIndex"],
                selected[-1]["endIndex"],
                snapshots,
                "".join(_text(p) for p in selected),
                tab_icon_emoji(title),
            )
        )
    return tuple(output)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _inventory(sections: tuple[TabSection, ...], lists_json: str) -> str:
    payload = _canonical(
        [
            [
                [
                    section.title,
                    section.icon_emoji,
                    section.start_index,
                    section.end_index,
                    section.text,
                    section.paragraphs,
                ]
                for section in sections
            ],
            lists_json,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
