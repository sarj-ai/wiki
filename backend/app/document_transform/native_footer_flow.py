from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
import hashlib
import json
from typing import Any, Literal, NamedTuple, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.ir import MAX_DOC_TABS
from app.document_transform.native_safety_diagnostics import (
    FooterShapeFailure,
    SafetyStage,
    native_safety_error,
)
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId


MAX_FOOTER_NAMED_STYLES = 60


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
    coverage: NativeFooterCoverage | None = None

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        coverage = self.coverage
        if coverage is None:
            return self
        entries = coverage.completed
        completed_ids = tuple(entry.tab_id for entry in entries)
        active_ids = (self.tab_id,) if coverage.active else ()
        processed = completed_ids + active_ids + coverage.pending
        skipped_ids = tuple(skip.tab_id for skip in coverage.skipped)
        all_ids = processed + skipped_ids
        markers = [
            marker for entry in entries for marker in (entry.create_marker, entry.populate_marker)
        ]
        markers.extend((self.create_marker, self.populate_marker))
        invalid_ids = (
            len(set(all_ids)) != len(all_ids)
            or len(set(coverage.topology)) != len(coverage.topology)
            or len(set(markers)) != len(markers)
            or set(all_ids) != set(coverage.topology)
            or tuple(tab for tab in coverage.topology if tab not in skipped_ids) != processed
        )
        invalid_entries = any(
            entry.coverage is not None or entry.stage is not NativeFooterStage.COMPLETE
            for entry in entries
        )
        invalid_inactive = not coverage.active and (
            bool(processed) or self.stage is not NativeFooterStage.COMPLETE
        )
        if not coverage.topology or invalid_ids or invalid_entries or invalid_inactive:
            msg = "Invalid bounded footer coverage"
            raise ValueError(msg)
        return self


class NativeFooterSkip(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tab_id: DocsTabId
    reason: Literal["custom_footer", "pageless", "unsupported_layout"]


class NativeFooterCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topology: tuple[DocsTabId, ...] = Field(max_length=MAX_DOC_TABS)
    pending: tuple[DocsTabId, ...] = Field(default=(), max_length=MAX_DOC_TABS)
    completed: tuple[NativeFooterCheckpoint, ...] = Field(default=(), max_length=MAX_DOC_TABS)
    skipped: tuple[NativeFooterSkip, ...] = Field(default=(), max_length=MAX_DOC_TABS)
    active: bool = True


def footer_coverage_complete(checkpoint: NativeFooterCheckpoint) -> bool:
    return checkpoint.stage is NativeFooterStage.COMPLETE and (
        checkpoint.coverage is None or not checkpoint.coverage.pending
    )


def verified_footer_coverage_projection(
    document: dict[str, Any], checkpoint: NativeFooterCheckpoint
) -> dict[str, Any]:
    if not footer_coverage_complete(checkpoint):
        native_safety_error("footer_coverage", "action")
    coverage = checkpoint.coverage
    if coverage is not None and not coverage.active:
        if footer_invariant_sha256(document) != checkpoint.invariant_sha256:
            native_safety_error("footer_coverage", "invariant")
        return deepcopy(document)
    projected, _ = verified_footer_projection(document, checkpoint, populated=True)
    if coverage is not None:
        for prior in reversed(coverage.completed):
            projected, _ = verified_footer_projection(projected, prior, populated=True)
    return projected


def footer_document_tab(document: dict[str, Any], tab_id: DocsTabId) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                matches.append(tab["documentTab"])
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    if len(matches) != 1:
        native_safety_error("footer_tab", "topology")
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
    stage: SafetyStage = "footer_projection_populated" if populated else "footer_projection_empty"
    snapshot = deepcopy(document)
    tab = footer_document_tab(snapshot, checkpoint.tab_id)
    style = tab.get("documentStyle", {})
    footer_id = style.get("defaultFooterId")
    if not isinstance(footer_id, str) or (
        checkpoint.footer_id is not None and footer_id != checkpoint.footer_id
    ):
        native_safety_error(stage, "segment")
    footers = tab.get("footers", {})
    footer = footers.get(footer_id)
    if isinstance(footer, dict) and footer.get("footerId", footer_id) != footer_id:
        native_safety_error(stage, "segment")
    if not isinstance(footer, dict) or not _footer_matches(
        footer,
        checkpoint.url,
        populated=populated,
        display_text=checkpoint.display_text,
        inherited_family=_normal_text_family(tab),
    ):
        native_safety_error(
            stage,
            "shape",
            footer_shape=_footer_shape_failure(
                footer,
                checkpoint.url,
                populated=populated,
                display_text=checkpoint.display_text,
                inherited_family=_normal_text_family(tab),
            ),
        )
    del footers[footer_id]
    if not footers:
        tab.pop("footers", None)
    del style["defaultFooterId"]
    _remove_footer_markers(snapshot, {checkpoint.create_marker, checkpoint.populate_marker})
    if footer_invariant_sha256(snapshot) != checkpoint.invariant_sha256:
        native_safety_error(stage, "invariant")
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
    footer: dict[str, Any],
    url: str,
    *,
    populated: bool,
    display_text: str | None = None,
    inherited_family: str | None = None,
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
            and not _link_style_matches(text_run.get("textStyle", {}), url, inherited_family)
        ):
            return False
    if populated:
        style = paragraph.get("paragraphStyle", {})
        if style.get("alignment") != "END" or style.get("namedStyleType") != "NORMAL_TEXT":
            return False
    caption = display_text if display_text is not None else url
    return "".join(parts) == (caption + "\n" if populated else "\n")


def _normal_text_family(tab: dict[str, Any]) -> str | None:
    # Require an explicit parent family; never guess editor defaults.
    named = tab.get("namedStyles")
    styles = named.get("styles") if isinstance(named, dict) else None
    if not isinstance(styles, list) or len(styles) > MAX_FOOTER_NAMED_STYLES:
        return None
    if any(not isinstance(style, dict) for style in styles):
        return None
    parents = [style for style in styles if style.get("namedStyleType") == "NORMAL_TEXT"]
    if len(parents) != 1:
        return None
    text = parents[0].get("textStyle")
    family = text.get("weightedFontFamily") if isinstance(text, dict) else None
    value = family.get("fontFamily") if isinstance(family, dict) else None
    return value if isinstance(value, str) and value else None


def _resolved_family(style: object, inherited_family: str | None) -> object:
    # Docs serializes equal-to-parent updates as absent fields. Never repair
    # an explicitly present malformed or conflicting weightedFontFamily.
    if not isinstance(style, dict):
        return None
    if "weightedFontFamily" not in style:
        return inherited_family
    family = style.get("weightedFontFamily")
    return family.get("fontFamily") if isinstance(family, dict) else None


def _link_style_matches(style: object, url: str, inherited_family: str | None = None) -> bool:
    profile = CURRENT_BRAND_MANIFEST.document
    return (
        isinstance(style, dict)
        and _resolved_family(style, inherited_family) == profile.font_family
        and style.get("fontSize") == {"magnitude": profile.caption_size_pt, "unit": "PT"}
        and style.get("link") == {"url": url}
        and not style.get("bold")
        and not style.get("italic")
    )


def _footer_shape_failure(
    footer: object,
    url: str,
    *,
    populated: bool,
    display_text: str | None = None,
    inherited_family: str | None = None,
) -> FooterShapeFailure | None:
    # Diagnostic mirror only: _footer_matches remains the unchanged authority.
    if not isinstance(footer, dict):
        return FooterShapeFailure("footer_container", "missing" if footer is None else "mismatch")
    if set(footer) - {"footerId", "content"}:
        return FooterShapeFailure("footer_fields", "mismatch")
    content = footer.get("content")
    if not isinstance(content, list):
        return FooterShapeFailure("content", "missing" if "content" not in footer else "mismatch")
    if len(content) != 1:
        return FooterShapeFailure("paragraph_count", "mismatch")
    element = content[0]
    if not isinstance(element, dict):
        return FooterShapeFailure("paragraph_container", "mismatch")
    if set(element) - {"startIndex", "endIndex", "paragraph"}:
        return FooterShapeFailure("paragraph_fields", "mismatch")
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict):
        return FooterShapeFailure(
            "paragraph_container", "missing" if "paragraph" not in element else "mismatch"
        )
    if set(paragraph) - {"elements", "paragraphStyle"}:
        return FooterShapeFailure("paragraph_fields", "mismatch")
    runs = paragraph.get("elements")
    if not isinstance(runs, list) or not runs:
        return FooterShapeFailure("runs", "missing" if "elements" not in paragraph else "mismatch")
    parts: list[str] = []
    for run in runs:
        failure = _footer_run_failure(
            run, url, populated=populated, inherited_family=inherited_family
        )
        if failure is not None:
            return failure
        text_run = run.get("textRun") if isinstance(run, dict) else None
        value = text_run.get("content") if isinstance(text_run, dict) else None
        if not isinstance(value, str):
            return FooterShapeFailure("text_content", "mismatch")
        parts.append(value)
    if populated:
        style = paragraph.get("paragraphStyle", {})
        if not isinstance(style, dict):
            return FooterShapeFailure("paragraph_fields", "mismatch")
        if style.get("alignment") != "END":
            return FooterShapeFailure(
                "alignment", "missing" if "alignment" not in style else "mismatch"
            )
        if style.get("namedStyleType") != "NORMAL_TEXT":
            return FooterShapeFailure(
                "named_style", "missing" if "namedStyleType" not in style else "mismatch"
            )
    caption = display_text if display_text is not None else url
    if "".join(parts) != (caption + "\n" if populated else "\n"):
        return FooterShapeFailure("caption", "mismatch")
    return None


def _footer_run_failure(
    run: object, url: str, *, populated: bool, inherited_family: str | None = None
) -> FooterShapeFailure | None:
    if not isinstance(run, dict):
        return FooterShapeFailure("run_container", "mismatch")
    if set(run) - {"startIndex", "endIndex", "textRun"}:
        return FooterShapeFailure("run_fields", "mismatch")
    text_run = run.get("textRun")
    if not isinstance(text_run, dict):
        return FooterShapeFailure("text_run", "missing" if "textRun" not in run else "mismatch")
    if set(text_run) - {"content", "textStyle"}:
        return FooterShapeFailure("text_run_fields", "mismatch")
    value = text_run.get("content")
    if not isinstance(value, str):
        return FooterShapeFailure(
            "text_content", "missing" if "content" not in text_run else "mismatch"
        )
    if populated and value.rstrip("\n"):
        return _footer_link_style_failure(text_run.get("textStyle", {}), url, inherited_family)
    return None


def _footer_link_style_failure(
    style: object, url: str, inherited_family: str | None = None
) -> FooterShapeFailure | None:
    if not isinstance(style, dict):
        return FooterShapeFailure("text_style", "mismatch")
    profile = CURRENT_BRAND_MANIFEST.document
    family = style.get("weightedFontFamily", {})
    if not isinstance(family, dict):
        return FooterShapeFailure("font_family", "mismatch")
    if _resolved_family(style, inherited_family) != profile.font_family:
        return FooterShapeFailure(
            "font_family", "missing" if "fontFamily" not in family else "mismatch"
        )
    if style.get("fontSize") != {"magnitude": profile.caption_size_pt, "unit": "PT"}:
        return FooterShapeFailure("font_size", "missing" if "fontSize" not in style else "mismatch")
    if style.get("link") != {"url": url}:
        return FooterShapeFailure("link", "missing" if "link" not in style else "mismatch")
    if style.get("bold"):
        return FooterShapeFailure("bold", "mismatch")
    if style.get("italic"):
        return FooterShapeFailure("italic", "mismatch")
    return None
