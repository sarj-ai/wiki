from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
import hashlib
import json
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import InPlaceNeedsReviewError
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId


class NativeFooterStage(StrEnum):
    CREATE = "create"
    POPULATE = "populate"
    COMPLETE = "complete"


class NativeFooterCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: NativeFooterStage = NativeFooterStage.CREATE
    tab_id: DocsTabId
    url: str
    # Missing on old durable checkpoints: retain their exact original payload.
    display_text: Literal["Open in Wiki"] | None = None
    revision_id: DocsRevisionId
    create_marker: str
    populate_marker: str
    marker_range: dict[str, Any]
    invariant_sha256: str
    footer_id: DocsSegmentId | None = None


def footer_document_tab(document: dict[str, Any], tab_id: DocsTabId) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                matches.append(tab["documentTab"])
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    if len(matches) != 1:
        raise InPlaceNeedsReviewError
    return matches[0]


def footer_invariant_sha256(document: dict[str, Any]) -> str:
    snapshot = deepcopy(document)
    snapshot.pop("revisionId", None)
    _remove_footer_markers(snapshot, set())
    _normalize_empty_footer_maps(snapshot.get("tabs", []))
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _normalize_empty_footer_maps(tabs: list[dict[str, Any]]) -> None:
    for tab in tabs:
        document_tab = tab.get("documentTab", {})
        if document_tab.get("footers") == {}:
            document_tab.pop("footers")
        _normalize_empty_footer_maps(tab.get("childTabs", []))


class VerifiedFooterProjection(NamedTuple):
    document: dict[str, Any]
    footer_id: DocsSegmentId


def verified_footer_projection(
    document: dict[str, Any],
    checkpoint: NativeFooterCheckpoint,
    *,
    populated: bool,
) -> VerifiedFooterProjection:
    snapshot = deepcopy(document)
    tab = footer_document_tab(snapshot, checkpoint.tab_id)
    style = tab.get("documentStyle", {})
    footer_id = style.get("defaultFooterId")
    if not isinstance(footer_id, str) or (
        checkpoint.footer_id is not None and footer_id != checkpoint.footer_id
    ):
        raise InPlaceNeedsReviewError
    footers = tab.get("footers", {})
    footer = footers.get(footer_id)
    if isinstance(footer, dict) and footer.get("footerId", footer_id) != footer_id:
        raise InPlaceNeedsReviewError
    if not isinstance(footer, dict) or not _footer_matches(
        footer, checkpoint.url, populated=populated, display_text=checkpoint.display_text
    ):
        raise InPlaceNeedsReviewError
    del footers[footer_id]
    if not footers:
        tab.pop("footers", None)
    del style["defaultFooterId"]
    _remove_footer_markers(snapshot, {checkpoint.create_marker, checkpoint.populate_marker})
    if footer_invariant_sha256(snapshot) != checkpoint.invariant_sha256:
        raise InPlaceNeedsReviewError
    return VerifiedFooterProjection(snapshot, DocsSegmentId(footer_id))


def _remove_footer_markers(value: object, markers: set[str]) -> None:
    match value:
        case list():
            for child in value:
                _remove_footer_markers(child, markers)
        case dict():
            ranges = value.get("namedRanges")
            if isinstance(ranges, dict):
                for marker in markers:
                    ranges.pop(marker, None)
                if not ranges:
                    value.pop("namedRanges", None)
            for child in value.values():
                _remove_footer_markers(child, markers)
        case _:
            pass


def _footer_matches(
    footer: dict[str, Any], url: str, *, populated: bool, display_text: str | None = None
) -> bool:
    if set(footer) - {"footerId", "content"}:
        return False
    content = footer.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return False
    element = content[0]
    if not isinstance(element, dict) or set(element) - {"startIndex", "endIndex", "paragraph"}:
        return False
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict) or set(paragraph) - {"elements", "paragraphStyle"}:
        return False
    runs = paragraph.get("elements")
    if not isinstance(runs, list) or not runs:
        return False
    parts: list[str] = []
    for run in runs:
        if not isinstance(run, dict) or set(run) - {"startIndex", "endIndex", "textRun"}:
            return False
        text_run = run.get("textRun")
        if not isinstance(text_run, dict) or set(text_run) - {"content", "textStyle"}:
            return False
        value = text_run.get("content")
        if not isinstance(value, str):
            return False
        parts.append(value)
        if (
            populated
            and value.rstrip("\n")
            and not _link_style_matches(text_run.get("textStyle", {}), url)
        ):
            return False
    if populated:
        style = paragraph.get("paragraphStyle", {})
        if style.get("alignment") != "END" or style.get("namedStyleType") != "NORMAL_TEXT":
            return False
    caption = display_text if display_text is not None else url
    return "".join(parts) == (caption + "\n" if populated else "\n")


def _link_style_matches(style: dict[str, Any], url: str) -> bool:
    profile = CURRENT_BRAND_MANIFEST.document
    return (
        style.get("weightedFontFamily", {}).get("fontFamily") == profile.font_family
        and style.get("fontSize") == {"magnitude": profile.caption_size_pt, "unit": "PT"}
        and style.get("link") == {"url": url}
        and not style.get("bold")
        and not style.get("italic")
    )
