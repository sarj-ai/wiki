from bisect import bisect_right
from dataclasses import dataclass, replace
from typing import Literal

from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, utf16_length
from app.document_transform.native_chip_detection import DetectedNativeSpan
from app.document_transform.native_quote_spans import (
    NativeInlineRange,
    native_inline_protected_ranges,
)
from app.document_transform.quotations import quoted_block_ids
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
        return replace(candidate, skip_reason=ranges[index].reason)
    return candidate
