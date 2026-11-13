from collections.abc import Awaitable, Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel

from app.document_transform.errors import InPlaceNeedsReviewError, SourceChangedError
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_chip_detection import (
    BlockedNativeSpan,
    DetectedNativeSpan,
    LocatedNativePlaceSpan,
    NativeDateProperties,
    NativeDateValue,
    detect_address_spans,
    detect_date_spans,
    native_chip_context,
)
from app.document_transform.native_chip_nodes import native_chip_node
from app.document_transform.native_chip_protection import (
    protect_native_chip_candidates,
    protected_native_chip_ranges,
)
from app.document_transform.readability_json import (
    array,
    bodies,
    document_digest,
    effective_text_style,
    integer,
    obj,
)
from app.identifiers import DocsRevisionId, DocsTabId, FileId


_MAX_CHIPS = 100
_MAX_MARKER_NAME = 128
_CHIP_STYLE_FIELDS = frozenset(
    {
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "smallCaps",
        "backgroundColor",
        "foregroundColor",
        "fontSize",
        "weightedFontFamily",
        "baselineOffset",
    }
)


class NativeChipSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tab_id: DocsTabId
    start: int
    end: int
    original_text: str
    text_style: dict[str, JsonValue]
    date_value: NativeDateValue


class _NativeDateRequest(RootModel[NativeDateProperties]):
    root: NativeDateProperties


class NativeChipSkip(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    tab_id: DocsTabId
    start: int
    end: int
    kind: Literal["date", "place"]
    reason: str


class NativeChipPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    planner_version: Literal[1, 2] = 1
    document_id: FileId
    source_revision_id: DocsRevisionId
    source_sha256: str
    before: dict[str, JsonValue]
    marker_name: str
    marker_range: dict[str, JsonValue]
    spans: tuple[NativeChipSpan, ...]
    skipped: tuple[NativeChipSkip, ...]
    requests: list[dict[str, JsonValue]]


class PendingNativeChipCheckpoint(NativeChipPlan):
    complete: Literal[False] = False
    revision_id: None = None


class CompletedNativeChipCheckpoint(NativeChipPlan):
    complete: Literal[True] = True
    revision_id: DocsRevisionId


NativeChipCheckpoint = Annotated[
    PendingNativeChipCheckpoint | CompletedNativeChipCheckpoint, Field(discriminator="complete")
]


def _paragraphs(value: JsonValue) -> Iterator[dict[str, JsonValue]]:
    match value:
        case dict():
            if "paragraph" in value:
                yield value
            else:
                for child in value.values():
                    yield from _paragraphs(child)
        case list():
            for child in value:
                yield from _paragraphs(child)
        case _:
            return


def plan_native_chips(
    document: dict[str, JsonValue], *, operation_id: uuid.UUID, planner_version: Literal[1, 2] = 2
) -> NativeChipCheckpoint | None:
    protected = protected_native_chip_ranges(
        parse_google_document(document, title=native_chip_node(document).title)
    )
    tabs = dict(bodies(document))
    context_text = "\n".join(
        _paragraph_text(p) for tab in tabs.values() for p in _paragraphs(native_chip_node(tab).body)
    )
    context = native_chip_context(str(native_chip_node(document).title), context_text)
    spans: list[NativeChipSpan] = []
    skipped: list[NativeChipSkip] = []
    for tab_id, tab in tabs.items():
        for paragraph in _paragraphs(native_chip_node(tab).body):
            text = _paragraph_text(paragraph)
            start = integer(paragraph["startIndex"])
            candidates = protect_native_chip_candidates(
                (
                    *detect_address_spans(text, context=context),
                    *detect_date_spans(text, context=context),
                ),
                text=text,
                tab_id=tab_id,
                paragraph_start=start,
                protected=protected,
                enabled=planner_version != 1,
            )
            for candidate in candidates:
                conversion = _candidate_conversion(tab_id, tab, paragraph, candidate)
                if isinstance(conversion, NativeChipSkip):
                    skipped.append(conversion)
                elif len(spans) >= _MAX_CHIPS:
                    skipped.append(
                        NativeChipSkip(
                            tab_id=tab_id,
                            start=start + candidate.start,
                            end=start + candidate.end,
                            kind=candidate.kind,
                            reason="conversion_limit",
                        )
                    )
                else:
                    spans.append(conversion)
    if not spans and not skipped:
        return None
    first_tab = next(iter(tabs))
    terminal = array(obj(tabs[first_tab]["body"])["content"])[-1]
    terminal_end = integer(obj(terminal)["endIndex"])
    ordered = tuple(sorted(spans, key=lambda span: (span.tab_id, -span.start)))
    return PendingNativeChipCheckpoint(
        planner_version=planner_version,
        document_id=FileId(str(document["documentId"])),
        source_revision_id=DocsRevisionId(str(document["revisionId"])),
        source_sha256=document_digest(document),
        before=deepcopy(document),
        marker_name=f"sarj_native_chips_{operation_id.hex}",
        marker_range={"tabId": first_tab, "startIndex": terminal_end - 1, "endIndex": terminal_end},
        spans=ordered,
        skipped=tuple(skipped),
        requests=chip_requests(ordered),
    )


def _candidate_conversion(
    tab_id: DocsTabId,
    tab: dict[str, JsonValue],
    paragraph: dict[str, JsonValue],
    candidate: DetectedNativeSpan,
) -> NativeChipSpan | NativeChipSkip:
    first = integer(paragraph["startIndex"]) + candidate.start
    end = integer(paragraph["startIndex"]) + candidate.end
    if isinstance(candidate, BlockedNativeSpan | LocatedNativePlaceSpan):
        return NativeChipSkip(
            tab_id=tab_id,
            start=first,
            end=end,
            kind=candidate.kind,
            reason=candidate.skip_reason
            if isinstance(candidate, BlockedNativeSpan)
            else "native_place_insertion_unsupported_by_verified_api",
        )
    reason: str | None = None
    run = _containing_text_run(paragraph, first, end)
    if reason is None and (run is None or _annotation_inside(tab, first, end)):
        reason = "style_or_annotation_overlap"
    style = obj(native_chip_node(obj(run["textRun"])).text_style) if run is not None else {}
    if reason is None and set(style) - _CHIP_STYLE_FIELDS:
        reason = "unsupported_or_linked_text_style"
    if reason is not None:
        return NativeChipSkip(
            tab_id=tab_id,
            start=first,
            end=end,
            kind=candidate.kind,
            reason=reason,
        )
    return NativeChipSpan(
        tab_id=tab_id,
        start=first,
        end=end,
        original_text=candidate.text,
        text_style=style,
        date_value=candidate.date_value,
    )


def _containing_text_run(
    paragraph: dict[str, JsonValue], start: int, end: int
) -> dict[str, JsonValue] | None:
    if set(paragraph) - {"startIndex", "endIndex", "paragraph"}:
        return None
    for value in array(obj(paragraph["paragraph"])["elements"]):
        part = obj(value)
        if (
            set(part) <= {"startIndex", "endIndex", "textRun"}
            and "textRun" in part
            and integer(part["startIndex"]) <= start < end <= integer(part["endIndex"])
        ):
            run = obj(part["textRun"])
            if set(run) <= {"content", "textStyle"}:
                return part
    return None


def _annotation_inside(tab: dict[str, JsonValue], start: int, end: int) -> bool:
    for group in obj(native_chip_node(tab).named_ranges).values():
        for definition in array(native_chip_node(obj(group)).named_ranges):
            for value in native_chip_node(obj(definition)).ranges:
                span = obj(value)
                if not native_chip_node(span).segment_id and any(
                    type(boundary) is int and start < boundary < end
                    for boundary in (
                        native_chip_node(span).start_index,
                        native_chip_node(span).end_index,
                    )
                ):
                    return True
    return False


def chip_requests(spans: tuple[NativeChipSpan, ...]) -> list[dict[str, JsonValue]]:
    requests: list[dict[str, JsonValue]] = []
    for span in spans:
        properties: dict[str, JsonValue] = _NativeDateRequest(
            span.date_value.properties()
        ).model_dump(mode="json")
        requests.extend(
            [
                {
                    "deleteContentRange": {
                        "range": {
                            "tabId": span.tab_id,
                            "startIndex": span.start,
                            "endIndex": span.end,
                        }
                    }
                },
                {
                    "insertDate": {
                        "location": {"tabId": span.tab_id, "index": span.start},
                        "dateElementProperties": properties,
                    }
                },
            ]
        )
        if span.text_style:
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {
                            "tabId": span.tab_id,
                            "startIndex": span.start,
                            "endIndex": span.start + 1,
                        },
                        "textStyle": span.text_style,
                        "fields": ",".join(sorted(span.text_style)),
                    }
                }
            )
    return requests


def _restore_indices(value: JsonValue, *, boundary: int, delta: int) -> None:
    match value:
        case dict():
            for key, child in value.items():
                if key in {"startIndex", "endIndex"} and type(child) is int and child >= boundary:
                    value[key] = child + delta
                elif isinstance(child, (dict, list)):
                    _restore_indices(child, boundary=boundary, delta=delta)
        case list():
            for child in value:
                _restore_indices(child, boundary=boundary, delta=delta)
        case _:
            return


def verify_native_chips(document: dict[str, JsonValue], checkpoint: NativeChipCheckpoint) -> None:
    _validate_chip_checkpoint(checkpoint)
    if native_chip_node(document).document_id != checkpoint.document_id:
        raise InPlaceNeedsReviewError
    if document_digest(checkpoint.before) != checkpoint.source_sha256:
        raise InPlaceNeedsReviewError
    if checkpoint.spans and not has_chip_marker(document, checkpoint):
        raise InPlaceNeedsReviewError
    projected = deepcopy(document)
    tabs = dict(bodies(projected))
    if checkpoint.requests != chip_requests(checkpoint.spans):
        raise InPlaceNeedsReviewError
    ids: set[str] = set()
    for tab_id, tab in tabs.items():
        ids.update(_project_chip_tab(tab_id, tab, checkpoint, document, ids))
    converted_paragraphs = frozenset(
        (tab_id, integer(node["startIndex"]))
        for tab_id, tab in bodies(checkpoint.before)
        for node in _paragraphs(native_chip_node(tab).body)
        if any(
            span.tab_id == tab_id
            and integer(node["startIndex"]) <= span.start < integer(node["endIndex"])
            for span in checkpoint.spans
        )
    )
    if document_digest(
        _coalesce_text_runs(projected, converted_paragraphs=converted_paragraphs)
    ) != document_digest(
        _coalesce_text_runs(checkpoint.before, converted_paragraphs=converted_paragraphs)
    ):
        raise InPlaceNeedsReviewError


def _project_chip_tab(
    tab_id: DocsTabId,
    tab: dict[str, JsonValue],
    checkpoint: NativeChipCheckpoint,
    document: dict[str, JsonValue],
    existing_ids: set[str],
) -> set[str]:
    targets = [span for span in checkpoint.spans if span.tab_id == tab_id]
    identifiers: set[str] = set()
    for span in targets:
        start = span.start - sum(
            other.end - other.start - 1 for other in targets if other.start < span.start
        )
        identifier = _restore_chip(
            tab, span, start=start, before=checkpoint.before, document=document
        )
        if identifier in existing_ids or identifier in identifiers:
            raise InPlaceNeedsReviewError
        identifiers.add(identifier)
    if tab_id == checkpoint.marker_range["tabId"]:
        obj(native_chip_node(tab).named_ranges).pop(checkpoint.marker_name, None)
    return identifiers


def _restore_chip(
    tab: dict[str, JsonValue],
    span: NativeChipSpan,
    *,
    start: int,
    before: dict[str, JsonValue],
    document: dict[str, JsonValue],
) -> str:
    matches = [
        (node, part)
        for node in _paragraphs(native_chip_node(tab).body)
        for value in array(obj(node["paragraph"])["elements"])
        if native_chip_node(part := obj(value)).start_index == start and "dateElement" in part
    ]
    if len(matches) != 1:
        raise InPlaceNeedsReviewError
    node, part = matches[0]
    identifier = _verify_chip_properties(part, span, start=start)
    if _chip_style(
        document, span.tab_id, node, obj(native_chip_node(obj(part["dateElement"])).text_style)
    ) != _source_style(before, span):
        raise InPlaceNeedsReviewError
    delta = span.end - span.start - 1
    _restore_indices(native_chip_node(tab).body, boundary=start + 1, delta=delta)
    _restore_annotations(tab, boundary=start + 1, delta=delta)
    part.pop("dateElement")
    part["textRun"] = {"content": span.original_text, "textStyle": span.text_style}
    return identifier


def _verify_chip_properties(part: dict[str, JsonValue], span: NativeChipSpan, *, start: int) -> str:
    chip = obj(part["dateElement"])
    properties = obj(native_chip_node(chip).date_properties)
    expected = span.date_value.properties()
    if (
        native_chip_node(part).end_index != start + 1
        or set(part) - {"startIndex", "endIndex", "dateElement"}
        or set(chip) - {"dateId", "textStyle", "dateElementProperties"}
        or {key: value for key, value in properties.items() if key != "displayText"} != expected
        or set(properties) - {*expected, "displayText"}
    ):
        raise InPlaceNeedsReviewError
    identifier = native_chip_node(chip).date_id
    if not isinstance(identifier, str) or not identifier:
        raise InPlaceNeedsReviewError
    return identifier


def _chip_style(
    document: dict[str, JsonValue],
    tab_id: DocsTabId,
    node: dict[str, JsonValue],
    style: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    tab = dict(bodies(document))[tab_id]
    return effective_text_style(
        document, tab, obj(native_chip_node(obj(node["paragraph"])).paragraph_style), style
    )


def _source_style(document: dict[str, JsonValue], span: NativeChipSpan) -> dict[str, JsonValue]:
    tab = dict(bodies(document))[span.tab_id]
    node = next(
        p
        for p in _paragraphs(native_chip_node(tab).body)
        if integer(p["startIndex"]) <= span.start < integer(p["endIndex"])
    )
    return _chip_style(document, span.tab_id, node, span.text_style)


def _restore_annotations(tab: dict[str, JsonValue], *, boundary: int, delta: int) -> None:
    for group in obj(native_chip_node(tab).named_ranges).values():
        for definition in array(native_chip_node(obj(group)).named_ranges):
            for value in native_chip_node(obj(definition)).ranges:
                if not native_chip_node(obj(value)).segment_id:
                    _restore_indices(value, boundary=boundary, delta=delta)


def has_chip_marker(document: dict[str, JsonValue], checkpoint: NativeChipCheckpoint) -> bool:
    for tab_id, tab in bodies(document):
        if tab_id != checkpoint.marker_range["tabId"]:
            continue
        named_ranges = obj(native_chip_node(tab).named_ranges)
        if checkpoint.marker_name not in named_ranges:
            return False
        marker = named_ranges[checkpoint.marker_name]
        definitions = array(native_chip_node(obj(marker)).named_ranges)
        if len(definitions) != 1:
            raise InPlaceNeedsReviewError
        ranges = native_chip_node(obj(definitions[0])).ranges
        shift = sum(
            span.end - span.start - 1
            for span in checkpoint.spans
            if span.tab_id == tab_id and span.end <= integer(checkpoint.marker_range["startIndex"])
        )
        actual = dict(obj(ranges[0])) if len(ranges) == 1 else {}
        # A range in a documentTab may omit its enclosing tabId. Both forms
        # identify exactly the same tab; a different explicit tab still fails.
        if native_chip_node(actual).tab_id == tab_id:
            actual.pop("tabId")
        if actual != {
            "startIndex": integer(checkpoint.marker_range["startIndex"]) - shift,
            "endIndex": integer(checkpoint.marker_range["endIndex"]) - shift,
        }:
            raise InPlaceNeedsReviewError
        return True
    return False


@dataclass(frozen=True, slots=True)
class NativeChipProvider:
    read: Callable[[FileId], Awaitable[dict[str, JsonValue]]]
    write: Callable[[FileId, NativeChipCheckpoint], Awaitable[None]]

    async def apply(self, checkpoint: NativeChipCheckpoint) -> CompletedNativeChipCheckpoint:
        _validate_chip_checkpoint(checkpoint)
        document = await self.read(checkpoint.document_id)
        if checkpoint.complete and not checkpoint.spans:
            if (
                native_chip_node(document).revision_id != checkpoint.revision_id
                or document_digest(document) != checkpoint.source_sha256
            ):
                raise SourceChangedError
        elif checkpoint.spans and has_chip_marker(document, checkpoint):
            verify_native_chips(document, checkpoint)
        else:
            if (
                checkpoint.complete
                or native_chip_node(document).revision_id != checkpoint.source_revision_id
                or document_digest(document) != checkpoint.source_sha256
            ):
                raise SourceChangedError
            if checkpoint.requests:
                document = await self._write_and_verify(checkpoint)
        if checkpoint.complete and native_chip_node(document).revision_id != checkpoint.revision_id:
            raise SourceChangedError
        return CompletedNativeChipCheckpoint.model_validate(
            {**checkpoint.model_dump(), "complete": True, "revision_id": document["revisionId"]}
        )

    async def _write_and_verify(self, checkpoint: NativeChipCheckpoint) -> dict[str, JsonValue]:
        if legacy_protected_conversion_count(checkpoint):
            raise InPlaceNeedsReviewError
        await self.write(checkpoint.document_id, checkpoint)
        document = await self.read(checkpoint.document_id)
        if not has_chip_marker(document, checkpoint):
            raise InPlaceNeedsReviewError
        verify_native_chips(document, checkpoint)
        return document


def _validate_chip_checkpoint(checkpoint: NativeChipCheckpoint) -> None:
    before = native_chip_node(checkpoint.before)
    if (
        before.document_id != checkpoint.document_id
        or before.revision_id != checkpoint.source_revision_id
        or document_digest(checkpoint.before) != checkpoint.source_sha256
        or not checkpoint.marker_name
        or len(checkpoint.marker_name) > _MAX_MARKER_NAME
    ):
        raise InPlaceNeedsReviewError
    planned = plan_native_chips(
        checkpoint.before,
        operation_id=uuid.UUID(int=0),
        planner_version=checkpoint.planner_version,
    )
    if (
        planned is None
        or planned.spans != checkpoint.spans
        or planned.skipped != checkpoint.skipped
        or planned.requests != checkpoint.requests
        or planned.marker_range != checkpoint.marker_range
    ):
        raise InPlaceNeedsReviewError


def legacy_protected_conversion_count(checkpoint: NativeChipCheckpoint) -> int:
    if checkpoint.planner_version != 1:
        return 0
    current = plan_native_chips(checkpoint.before, operation_id=uuid.UUID(int=0))
    current_spans = current.spans if current is not None else ()
    return sum(span not in current_spans for span in checkpoint.spans)


def _paragraph_text(element: dict[str, JsonValue]) -> str:
    result = []
    cursor = integer(element["startIndex"])
    for value in array(obj(element["paragraph"])["elements"]):
        part = obj(value)
        if (
            native_chip_node(part).start_index != cursor
            or type(native_chip_node(part).end_index) is not int
        ):
            raise InPlaceNeedsReviewError
        if "textRun" in part:
            text = obj(part["textRun"])["content"]
            if not isinstance(text, str) or utf16_length(text) != integer(
                part["endIndex"]
            ) - integer(cursor):
                raise InPlaceNeedsReviewError
            result.append(text)
        else:
            result.append("\ufffc" * (integer(part["endIndex"]) - integer(cursor)))
        cursor = integer(part["endIndex"])
    if cursor != element["endIndex"]:
        raise InPlaceNeedsReviewError
    return "".join(result)


def _coalesce_text_runs(
    document: dict[str, JsonValue],
    *,
    converted_paragraphs: frozenset[tuple[DocsTabId, int]] = frozenset(),
) -> dict[str, JsonValue]:
    result = deepcopy(document)
    for tab_id, tab in bodies(result):
        if native_chip_node(tab).named_ranges == {}:
            tab.pop("namedRanges", None)
        for node in _paragraphs(native_chip_node(tab).body):
            paragraph = obj(node["paragraph"])
            merged: list[JsonValue] = []
            for value in _comparison_elements(
                result,
                tab,
                node,
                converted=(tab_id, integer(node["startIndex"])) in converted_paragraphs,
            ):
                part = obj(value)
                previous = obj(merged[-1]) if merged else None
                if (
                    previous is not None
                    and _plain_run(previous)
                    and _plain_run(part)
                    and previous["endIndex"] == part["startIndex"]
                    and native_chip_node(obj(previous["textRun"])).text_style
                    == native_chip_node(obj(part["textRun"])).text_style
                ):
                    obj(previous["textRun"])["content"] = str(
                        obj(previous["textRun"])["content"]
                    ) + str(obj(part["textRun"])["content"])
                    previous["endIndex"] = part["endIndex"]
                else:
                    merged.append(part)
            paragraph["elements"] = merged
    return result


def _comparison_elements(
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    node: dict[str, JsonValue],
    *,
    converted: bool,
) -> list[JsonValue]:
    paragraph = obj(node["paragraph"])
    elements = array(paragraph["elements"])
    if not converted:
        return elements
    # Inserting a chip can remove redundant inherited fields from adjacent
    # text, including a table cell's newline. Only converted paragraphs use
    # effective styles; the full parent styles and unknown fields stay hashed.
    for value in elements:
        part = obj(value)
        if _plain_run(part):
            run = obj(part["textRun"])
            run["textStyle"] = effective_text_style(
                document,
                tab,
                obj(native_chip_node(paragraph).paragraph_style),
                obj(native_chip_node(run).text_style),
            )
    return elements


def _plain_run(value: dict[str, JsonValue]) -> bool:
    return (
        set(value) <= {"startIndex", "endIndex", "textRun"}
        and "textRun" in value
        and set(obj(value["textRun"])) <= {"content", "textStyle"}
    )
