from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import chain
import json
from typing import TYPE_CHECKING, Any, Literal, NewType, Protocol, TypedDict
import uuid

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel

from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.ir import parse_google_document, verify_planned_date_conversions
from app.document_transform.native_elements import NativeElementProposal, plan_native_elements
from app.document_transform.native_payload import stable_native_payload
from app.identifiers import DocsRevisionId, DocsTabId


if TYPE_CHECKING:
    from collections.abc import ItemsView, Iterator

    from app.document_transform.brand import DocsRequest


ANCHOR_END = 2
DATE_TEXT_WIDTH = 10
CHIP_PARAGRAPH_ELEMENTS = 2
NativeDateBlockId = NewType("NativeDateBlockId", str)


class _DateTextRun(TypedDict, total=False):
    content: str
    textStyle: JsonValue


class _DateParagraphElement(TypedDict, total=False):
    startIndex: int
    endIndex: int
    textRun: _DateTextRun
    dateElement: JsonValue


class _DateParagraph(TypedDict, total=False):
    elements: list[_DateParagraphElement]


class _DateContent(Protocol):
    def get(
        self, key: Literal["startIndex", "endIndex"], default: None = None, /
    ) -> int | None: ...

    def __getitem__(self, key: Literal["paragraph"], /) -> _DateParagraph: ...

    def __contains__(self, key: str, /) -> bool: ...

    def __iter__(self) -> Iterator[str]: ...


type _DateBodyContent = list[_DateContent]


class _DateBody(Protocol):
    def get(self, key: Literal["content"], default: list[_DateContent], /) -> _DateBodyContent: ...

    def items(self) -> ItemsView[str, JsonValue]: ...

    def __setitem__(self, key: str, value: JsonValue, /) -> None: ...


class _DateEmptyBody(TypedDict, total=False):
    content: _DateBodyContent


class _DateRangeDefinition(TypedDict, total=False):
    ranges: list[dict[str, JsonValue]]


class _DateRangeGroup(TypedDict, total=False):
    namedRanges: list[_DateRangeDefinition]


class _DateTabPayload(TypedDict, total=False):
    body: _DateBody
    namedRanges: dict[str, _DateRangeGroup]


class _DateTabProperties(TypedDict, total=False):
    tabId: DocsTabId


class _DateTab(TypedDict, total=False):
    tabProperties: _DateTabProperties
    documentTab: _DateTabPayload
    childTabs: list[_DateTab]


class _DateDocument(Protocol):
    def get(self, key: Literal["tabs"], default: list[_DateTab], /) -> list[_DateTab]: ...


class NativeDateParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tab_id: DocsTabId
    block_id: NativeDateBlockId
    source_sha256: str
    paragraph: dict[str, Any]


class NativeDatePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: DocsRevisionId
    marker_name: str
    marker_range: dict[str, Any]
    invariant_sha256: str
    invariant_version: Literal["stable-images-v2"] | None = None
    paragraphs: list[NativeDateParagraph]
    requests: list[dict[str, Any]]


class PendingNativeDateCheckpoint(NativeDatePlan):
    complete: Literal[False] = False
    declined_reason: None = None


class DeclinedNativeDateCheckpoint(NativeDatePlan):
    complete: Literal[False] = False
    declined_reason: Literal["provider_rejected"] = "provider_rejected"


class CompletedNativeDateCheckpoint(NativeDatePlan):
    complete: Literal[True] = True
    declined_reason: None = None


NativeDateCheckpoint = (
    PendingNativeDateCheckpoint | DeclinedNativeDateCheckpoint | CompletedNativeDateCheckpoint
)


class NativeDatePayload(RootModel[NativeDateCheckpoint]):
    pass


def decline_dates(checkpoint: PendingNativeDateCheckpoint) -> DeclinedNativeDateCheckpoint:
    return DeclinedNativeDateCheckpoint.model_validate(
        {**checkpoint.model_dump(), "declined_reason": "provider_rejected"}
    )


def complete_dates(
    checkpoint: NativeDateCheckpoint, revision_id: DocsRevisionId
) -> CompletedNativeDateCheckpoint:
    return CompletedNativeDateCheckpoint.model_validate(
        {**checkpoint.model_dump(), "complete": True, "revision_id": revision_id}
    )


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


def _tabs(document: _DateDocument) -> list[_DateTab]:
    result = []

    def visit(tabs: list[_DateTab]) -> None:
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


def versioned_date_invariant_sha256(
    document: JsonValue, *, version: Literal["stable-images-v2"] | None
) -> str:
    return date_invariant_sha256(_date_document_payload(document, version=version))


def _date_document_payload(
    document: JsonValue, *, version: Literal["stable-images-v2"] | None
) -> dict[str, JsonValue]:
    payload = stable_native_payload(document) if version == "stable-images-v2" else document
    if not isinstance(payload, dict):
        msg = "A date invariant requires a document object"
        raise TypeError(msg)
    return payload


def plan_copy_dates(
    document: dict[str, Any], *, operation_id: uuid.UUID
) -> NativeDateCheckpoint | None:
    proposals = plan_native_elements(parse_google_document(document, title=""))
    paragraphs: list[NativeDateParagraph] = []
    requests: list[DocsRequest] = []
    tabs = _tabs(document)

    def _date_range_has_annotation(
        proposal: NativeElementProposal,
    ) -> bool:
        source = proposal.source_range
        matching_tabs = (
            tab for tab in tabs if tab.get("tabProperties", {}).get("tabId") == source.tab_id
        )
        groups = chain.from_iterable(
            tab.get("documentTab", {}).get("namedRanges", {}).values() for tab in matching_tabs
        )
        definitions = chain.from_iterable(group.get("namedRanges", []) for group in groups)
        spans = chain.from_iterable(named.get("ranges", []) for named in definitions)
        boundaries = [
            boundary
            for span in spans
            if not span.get("segmentId")
            for boundary in (span.get("startIndex"), span.get("endIndex"))
        ]
        return any(
            isinstance(boundary, int) and source.start_index < boundary < source.end_index
            for boundary in boundaries
        )

    # A marker before every replacement retains its exact range after shrinking
    # chip insertion. The first paragraph is deliberately never converted.
    if not tabs:
        return None
    anchor_tab = tabs[0].get("tabProperties", {}).get("tabId")
    first_body = tabs[0].get("documentTab", {}).get("body", _DateEmptyBody()).get("content", [])
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
        if proposal.source_range.start_index <= ANCHOR_END:
            continue
        if _date_range_has_annotation(proposal):
            continue
        paragraph = _date_proposal_paragraph(tabs, proposal)
        if paragraph is None:
            continue
        paragraphs.append(paragraph)
        requests.extend(proposal.requests())
    if not paragraphs:
        return None
    return PendingNativeDateCheckpoint(
        revision_id=DocsRevisionId(document["revisionId"]),
        marker_name=f"sarj_native_dates_{operation_id.hex}",
        marker_range={"tabId": anchor_tab, "startIndex": anchor_end - 1, "endIndex": anchor_end},
        invariant_sha256=date_invariant_sha256(document),
        paragraphs=paragraphs,
        requests=requests,
    )


def _date_proposal_paragraph(
    tabs: list[_DateTab],
    proposal: NativeElementProposal,
) -> NativeDateParagraph | None:
    source = proposal.source_range
    matches = [
        item
        for tab in tabs
        if tab.get("tabProperties", {}).get("tabId") == source.tab_id
        for item in tab.get("documentTab", {}).get("body", _DateEmptyBody()).get("content", [])
        if item.get("startIndex") == source.start_index and "paragraph" in item
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    if set(item) - {"startIndex", "endIndex", "paragraph"}:
        return None
    elements = item["paragraph"].get("elements", [])
    # Exact single-run paragraph preserves every preexisting style and
    # rejects suggestions, links and other unexpected element properties.
    if len(elements) != 1 or set(elements[0]) - {"startIndex", "endIndex", "textRun"}:
        return None
    run = elements[0].get("textRun", {})
    if set(run) - {"content", "textStyle"} or run.get("content") != proposal.original_text + "\n":
        return None
    return NativeDateParagraph.model_validate(
        {
            "tab_id": source.tab_id,
            "block_id": NativeDateBlockId(proposal.block_id),
            "source_sha256": proposal.source_sha256,
            "paragraph": deepcopy(item),
        }
    )


def verified_date_projection(
    document: dict[str, Any], checkpoint: NativeDateCheckpoint
) -> dict[str, Any]:
    if checkpoint.declined_reason is not None:
        verify_dates_unchanged(document, checkpoint)
        return deepcopy(document)
    projected = deepcopy(document)
    for tab in _tabs(projected):
        _restore_date_tab(tab, checkpoint)
    if date_invariant_sha256(projected) != checkpoint.invariant_sha256:
        raise InPlaceNeedsReviewError
    if not verify_planned_date_conversions(
        parse_google_document(projected, title=""),
        parse_google_document(document, title=""),
        source_block_hashes={item.block_id: item.source_sha256 for item in checkpoint.paragraphs},
    ):
        raise InPlaceNeedsReviewError
    return projected


def _restore_date_tab(tab: _DateTab, checkpoint: NativeDateCheckpoint) -> None:
    tab_id = tab.get("tabProperties", {}).get("tabId")
    payload = tab.get("documentTab", {})
    markers = payload.get("namedRanges", {})
    markers.pop(checkpoint.marker_name, None)
    planned = sorted(
        (item for item in checkpoint.paragraphs if item.tab_id == tab_id),
        key=lambda item: item.paragraph["startIndex"],
    )
    if not planned:
        return
    shifts: list[tuple[int, int, int]] = []
    total = 0
    body: _DateBodyContent = payload.get("body", _DateEmptyBody()).get("content", [])
    replacements = []
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
        width = _verified_date_chip_width(actual, item, start)
        delta = DATE_TEXT_WIDTH - width
        shifts.append((start, start + width, delta))
        total += delta
        replacements.append((index, deepcopy(original)))
    _restore_indices(payload.get("body", {}), shifts)
    definitions = chain.from_iterable(group.get("namedRanges", []) for group in markers.values())
    spans = chain.from_iterable(named.get("ranges", []) for named in definitions)
    for span in spans:
        if not span.get("segmentId"):
            _restore_indices(span, shifts)
    for index, original in replacements:
        body[index] = original


def _verified_date_chip_width(
    actual: _DateContent,
    planned: NativeDateParagraph,
    start: int,
) -> int:
    original = planned.paragraph
    elements = actual["paragraph"].get("elements", [])
    if (
        len(elements) != CHIP_PARAGRAPH_ELEMENTS
        or "dateElement" not in elements[0]
        or elements[1].get("textRun", {}).get("content") != "\n"
    ):
        raise InPlaceNeedsReviewError
    width: int = elements[0].get("endIndex", 0) - elements[0].get("startIndex", 0)
    if (
        not 1 <= width <= DATE_TEXT_WIDTH
        or elements[0].get("startIndex") != start
        or elements[1].get("startIndex") != start + width
        or elements[1].get("endIndex") != start + width + 1
        or actual.get("endIndex") != start + width + 1
    ):
        raise InPlaceNeedsReviewError
    # Keep newline formatting and all paragraph properties exact.
    if elements[1].get("textRun", {}).get("textStyle", {}) != original["paragraph"]["elements"][0][
        "textRun"
    ].get("textStyle", {}):
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
    return width


def plan_stable_copy_dates(
    document: JsonValue, *, operation_id: uuid.UUID
) -> NativeDateCheckpoint | None:
    checkpoint = plan_copy_dates(
        _date_document_payload(document, version="stable-images-v2"), operation_id=operation_id
    )
    if checkpoint is None:
        return None
    return checkpoint.model_copy(update={"invariant_version": "stable-images-v2"})


def verified_versioned_date_projection(
    document: JsonValue, checkpoint: NativeDateCheckpoint
) -> dict[str, JsonValue]:
    return verified_date_projection(
        _date_document_payload(document, version=checkpoint.invariant_version), checkpoint
    )


def verify_versioned_dates_unchanged(document: JsonValue, checkpoint: NativeDateCheckpoint) -> None:
    verify_dates_unchanged(
        _date_document_payload(document, version=checkpoint.invariant_version), checkpoint
    )


def _restore_indices(value: JsonValue | _DateBody, shifts: list[tuple[int, int, int]]) -> None:
    match value:
        case list():
            for child in value:
                if isinstance(child, (dict, list)):
                    _restore_indices(child, shifts)
        case dict():
            for key, child in value.items():
                if key in {"startIndex", "endIndex"} and isinstance(child, int):
                    value[key] = _restored_date_index(child, shifts)
                elif isinstance(child, (dict, list)):
                    _restore_indices(child, shifts)
        case _:
            pass


def _restored_date_index(index: int, shifts: list[tuple[int, int, int]]) -> int:
    if any(start < index < end for start, end, _ in shifts):
        raise InPlaceNeedsReviewError
    return index + sum(delta for _, end, delta in shifts if index >= end)
