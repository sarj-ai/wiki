from bisect import bisect_right
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import JsonValue

from app.document_transform.code_syntax import is_code_font_family
from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, utf16_length
from app.document_transform.native_chip_detection import BlockedNativeSpan, DetectedNativeSpan
from app.document_transform.native_chip_nodes import native_chip_node
from app.document_transform.native_font_context import resolve_native_font_family
from app.document_transform.native_quote_spans import (
    NativeInlineRange,
    native_inline_protected_ranges,
)
from app.document_transform.quotations import quoted_block_ids
from app.document_transform.readability_json import array, integer, obj
from app.identifiers import DocsTabId


@dataclass(frozen=True, slots=True)
class ProtectedNativeChipRange:
    source: SourceRange
    reason: Literal["protected_quotation", "protected_code"]


def protected_native_chip_ranges(document: DocumentIR) -> tuple[ProtectedNativeChipRange, ...]:
    quotations = quoted_block_ids(document)
    result = []
    for block in document.blocks:
        if block.source_range is None:
            continue
        if block.kind is BlockKind.CODE:
            result.append(ProtectedNativeChipRange(block.source_range, "protected_code"))
        elif block.kind is BlockKind.QUOTE or block.id in quotations:
            result.append(ProtectedNativeChipRange(block.source_range, "protected_quotation"))
    return tuple(result)


def native_code_chip_ranges(
    document: dict[str, JsonValue], *, tab_id: DocsTabId, node: dict[str, JsonValue], text: str
) -> tuple[NativeInlineRange, ...]:
    ranges = [
        item
        for item in native_inline_protected_ranges(text)
        if item.reason in {"protected_code", "unclosed_code"}
    ]
    start = integer(node["startIndex"])
    paragraph = obj(node["paragraph"])
    for value in array(paragraph["elements"]):
        part = obj(value)
        if "textRun" not in part:
            continue
        family = resolve_native_font_family(
            document,
            tab_id,
            native_chip_node(paragraph).paragraph_style,
            native_chip_node(obj(part["textRun"])).text_style,
        )
        if family is not None and is_code_font_family(family):
            ranges.append(
                NativeInlineRange(
                    integer(part["startIndex"]) - start,
                    integer(part["endIndex"]) - start,
                    "protected_code",
                )
            )
    return _merged_ranges(ranges)


def mask_native_code_text(text: str, ranges: tuple[NativeInlineRange, ...]) -> str:
    value = bytearray(text.encode("utf-16-le"))
    for item in ranges:
        if item.start < 0 or item.end > len(value) // 2:
            raise InPlaceNeedsReviewError
        # A hard barrier prevents a date in code from anchoring prose times.
        # Equal UTF-16 length keeps every eligible prose span source-bound.
        value[item.start * 2 : item.end * 2] = b"\xfc\xff" * (item.end - item.start)
    return value.decode("utf-16-le")


def protect_native_code_candidates(
    original: tuple[DetectedNativeSpan, ...],
    prose: tuple[DetectedNativeSpan, ...],
    ranges: tuple[NativeInlineRange, ...],
) -> tuple[DetectedNativeSpan, ...]:
    starts = [item.start for item in ranges]
    selected = tuple(_protect_candidate(item, ranges, starts) for item in prose)
    keys = {(item.start, item.end, item.kind) for item in selected}
    return (
        *selected,
        *(
            BlockedNativeSpan(
                item.start,
                item.end,
                item.text,
                item.kind,
                "protected_code"
                if _protect_candidate(item, ranges, starts) != item
                else "protected_code_context",
            )
            for item in original
            if (item.start, item.end, item.kind) not in keys
        ),
    )


def protect_native_chip_candidates(
    candidates: tuple[DetectedNativeSpan, ...],
    *,
    text: str,
    tab_id: DocsTabId,
    paragraph_start: int,
    protected: tuple[ProtectedNativeChipRange, ...],
    enabled: bool,
) -> tuple[DetectedNativeSpan, ...]:
    if not enabled:
        return candidates
    length = utf16_length(text)
    ranges = list(native_inline_protected_ranges(text))
    ranges.extend(
        NativeInlineRange(
            max(0, item.source.start_index - paragraph_start),
            min(length, item.source.end_index - paragraph_start),
            item.reason,
        )
        for item in protected
        if item.source.tab_id == tab_id
        and item.source.segment_id is None
        and paragraph_start < item.source.end_index
        and paragraph_start + length > item.source.start_index
    )
    merged = _merged_ranges(ranges)
    starts = [item.start for item in merged]
    return tuple(_protect_candidate(candidate, merged, starts) for candidate in candidates)


def _merged_ranges(ranges: list[NativeInlineRange]) -> tuple[NativeInlineRange, ...]:
    result: list[NativeInlineRange] = []
    for item in sorted(ranges, key=lambda item: (item.start, item.end)):
        if result and item.start < result[-1].end:
            result[-1] = replace(result[-1], end=max(result[-1].end, item.end))
        else:
            result.append(item)
    return tuple(result)


def _protect_candidate(
    candidate: DetectedNativeSpan, ranges: tuple[NativeInlineRange, ...], starts: list[int]
) -> DetectedNativeSpan:
    index = bisect_right(starts, candidate.end - 1) - 1
    if index >= 0 and ranges[index].end > candidate.start:
        return BlockedNativeSpan(
            candidate.start,
            candidate.end,
            candidate.text,
            candidate.kind,
            ranges[index].reason,
        )
    return candidate
