from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Literal, NewType, Self
import uuid

from pydantic import BaseModel, ConfigDict, model_validator

from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.ir import parse_google_document, verify_planned_date_conversions
from app.document_transform.native_elements import plan_native_elements
from app.identifiers import DocsRevisionId, DocsTabId


ANCHOR_END = 2
DATE_TEXT_WIDTH = 10
CHIP_PARAGRAPH_ELEMENTS = 2
NativeDateBlockId = NewType("NativeDateBlockId", str)


class NativeDateParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tab_id: DocsTabId
    block_id: NativeDateBlockId
    source_sha256: str
    paragraph: dict[str, Any]


class NativeDateCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: DocsRevisionId
    marker_name: str
    marker_range: dict[str, Any]
    invariant_sha256: str
    paragraphs: list[NativeDateParagraph]
    requests: list[dict[str, Any]]
    complete: bool = False
    declined_reason: Literal["provider_rejected"] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.complete and self.declined_reason is not None:
            msg = "A declined date conversion cannot be complete"
            raise ValueError(msg)
        return self


def verify_dates_unchanged(document: dict[str, Any], checkpoint: NativeDateCheckpoint) -> None:
    # Check every provider surface, including malformed or misplaced markers.
    def contains_marker(value: object) -> bool:
        match value:
            case dict():
                return checkpoint.marker_name in value or any(
                    contains_marker(item) for item in value.values()
                )
            case list():
                return any(contains_marker(item) for item in value)
            case _:
                return value == checkpoint.marker_name

    if (
        document.get("revisionId") != checkpoint.revision_id
        or contains_marker(document)
        or date_invariant_sha256(document) != checkpoint.invariant_sha256
    ):
        raise InPlaceNeedsReviewError


def _tabs(document: dict[str, Any]) -> list[dict[str, Any]]:
    result = []

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            result.append(tab)
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    return result


def date_invariant_sha256(document: dict[str, Any]) -> str:
    snapshot = deepcopy(document)
    snapshot.pop("revisionId", None)
    for tab in _tabs(snapshot):
        if tab.get("documentTab", {}).get("namedRanges") == {}:
            tab["documentTab"].pop("namedRanges")
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def plan_copy_dates(
    document: dict[str, Any], *, operation_id: uuid.UUID
) -> NativeDateCheckpoint | None:
    proposals = plan_native_elements(parse_google_document(document, title=""))
    paragraphs: list[NativeDateParagraph] = []
    requests: list[dict[str, Any]] = []
    tabs = _tabs(document)
    # A marker before every replacement retains its exact range after shrinking
    # chip insertion. The first paragraph is deliberately never converted.
    if not tabs:
        return None
    anchor_tab = tabs[0].get("tabProperties", {}).get("tabId")
    first_body = tabs[0].get("documentTab", {}).get("body", {}).get("content", [])
    anchors = [item for item in first_body if item.get("startIndex") == 1 and "paragraph" in item]
    if len(anchors) != 1:
        return None
    anchor_end = anchors[0].get("endIndex")
    anchor_elements = anchors[0]["paragraph"].get("elements", [])
    if (
        not isinstance(anchor_end, int)
        or anchor_end <= 1
        or not anchor_elements
        or not anchor_elements[-1].get("textRun", {}).get("content", "").endswith("\n")
    ):
        return None
    for proposal in proposals:
        source = proposal.source_range
        if source.start_index <= ANCHOR_END:
            continue
        # Existing annotations whose boundary lies inside replaced text cannot
        # be reconstructed from a chip's shorter provider index range.
        boundaries = [
            boundary
            for tab in tabs
            if tab.get("tabProperties", {}).get("tabId") == source.tab_id
            for group in tab.get("documentTab", {}).get("namedRanges", {}).values()
            for named in group.get("namedRanges", [])
            for span in named.get("ranges", [])
            if not span.get("segmentId")
            for boundary in (span.get("startIndex"), span.get("endIndex"))
        ]
        if any(
            isinstance(boundary, int) and source.start_index < boundary < source.end_index
            for boundary in boundaries
        ):
            continue
        matches = [
            item
            for tab in tabs
            if tab.get("tabProperties", {}).get("tabId") == source.tab_id
            for item in tab.get("documentTab", {}).get("body", {}).get("content", [])
            if item.get("startIndex") == source.start_index and "paragraph" in item
        ]
        if len(matches) != 1:
            continue
        item = matches[0]
        if set(item) - {"startIndex", "endIndex", "paragraph"}:
            continue
        elements = item["paragraph"].get("elements", [])
        # Exact single-run paragraph preserves every preexisting style and
        # rejects suggestions, links and other unexpected element properties.
        if len(elements) != 1 or set(elements[0]) - {"startIndex", "endIndex", "textRun"}:
            continue
        run = elements[0].get("textRun", {})
        if (
            set(run) - {"content", "textStyle"}
            or run.get("content") != proposal.original_text + "\n"
        ):
            continue
        paragraphs.append(
            NativeDateParagraph(
                tab_id=source.tab_id,
                block_id=NativeDateBlockId(proposal.block_id),
                source_sha256=proposal.source_sha256,
                paragraph=deepcopy(item),
            )
        )
        requests.extend(proposal.requests())
    if not paragraphs:
        return None
    return NativeDateCheckpoint(
        revision_id=DocsRevisionId(document["revisionId"]),
        marker_name=f"sarj_native_dates_{operation_id.hex}",
        marker_range={"tabId": anchor_tab, "startIndex": anchor_end - 1, "endIndex": anchor_end},
        invariant_sha256=date_invariant_sha256(document),
        paragraphs=paragraphs,
        requests=requests,
    )


def verified_date_projection(
    document: dict[str, Any], checkpoint: NativeDateCheckpoint
) -> dict[str, Any]:
    if checkpoint.declined_reason is not None:
        verify_dates_unchanged(document, checkpoint)
        return deepcopy(document)
    projected = deepcopy(document)
    for tab in _tabs(projected):
        tab_id = tab.get("tabProperties", {}).get("tabId")
        payload = tab.get("documentTab", {})
        markers = payload.get("namedRanges", {})
        markers.pop(checkpoint.marker_name, None)
        planned = sorted(
            (item for item in checkpoint.paragraphs if item.tab_id == tab_id),
            key=lambda item: item.paragraph["startIndex"],
        )
        if not planned:
            continue
        shifts: list[tuple[int, int, int]] = []
        total = 0
        body = payload.get("body", {}).get("content", [])
        replacements: list[tuple[int, dict[str, Any]]] = []
        for item in planned:
            original = item.paragraph
            start = original["startIndex"] - total
            matches = [
                (index, value)
                for index, value in enumerate(body)
                if value.get("startIndex") == start and "paragraph" in value
            ]
            if len(matches) != 1:
                raise InPlaceNeedsReviewError
            index, actual = matches[0]
            elements = actual["paragraph"].get("elements", [])
            if (
                len(elements) != CHIP_PARAGRAPH_ELEMENTS
                or "dateElement" not in elements[0]
                or elements[1].get("textRun", {}).get("content") != "\n"
            ):
                raise InPlaceNeedsReviewError
            width = elements[0].get("endIndex", 0) - elements[0].get("startIndex", 0)
            if (
                not 1 <= width <= DATE_TEXT_WIDTH
                or elements[0].get("startIndex") != start
                or elements[1].get("startIndex") != start + width
                or elements[1].get("endIndex") != start + width + 1
                or actual.get("endIndex") != start + width + 1
            ):
                raise InPlaceNeedsReviewError
            # Keep newline formatting and all paragraph properties exact.
            if elements[1].get("textRun", {}).get("textStyle", {}) != original["paragraph"][
                "elements"
            ][0]["textRun"].get("textStyle", {}):
                raise InPlaceNeedsReviewError
            if {key: value for key, value in actual["paragraph"].items() if key != "elements"} != {
                key: value for key, value in original["paragraph"].items() if key != "elements"
            }:
                raise InPlaceNeedsReviewError
            if (
                set(actual) != set(original)
                or set(elements[1]) - {"startIndex", "endIndex", "textRun"}
                or set(elements[1]["textRun"]) - {"content", "textStyle"}
            ):
                raise InPlaceNeedsReviewError
            delta = DATE_TEXT_WIDTH - width
            shifts.append((start, start + width, delta))
            total += delta
            replacements.append((index, deepcopy(original)))
        _restore_indices(payload.get("body", {}), shifts)
        for group in markers.values():
            for named in group.get("namedRanges", []):
                for span in named.get("ranges", []):
                    if not span.get("segmentId"):
                        _restore_indices(span, shifts)
        for index, original in replacements:
            body[index] = original
    if date_invariant_sha256(projected) != checkpoint.invariant_sha256:
        raise InPlaceNeedsReviewError
    if not verify_planned_date_conversions(
        parse_google_document(projected, title=""),
        parse_google_document(document, title=""),
        source_block_hashes={item.block_id: item.source_sha256 for item in checkpoint.paragraphs},
    ):
        raise InPlaceNeedsReviewError
    return projected


def _restore_indices(value: dict[str, Any] | list[Any], shifts: list[tuple[int, int, int]]) -> None:
    match value:
        case list():
            for child in value:
                if isinstance(child, (dict, list)):
                    _restore_indices(child, shifts)
        case dict():
            for key, child in value.items():
                if key in {"startIndex", "endIndex"} and isinstance(child, int):
                    if any(start < child < end for start, end, _ in shifts):
                        raise InPlaceNeedsReviewError
                    value[key] = child + sum(delta for _, end, delta in shifts if child >= end)
                elif isinstance(child, (dict, list)):
                    _restore_indices(child, shifts)
