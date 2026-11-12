from dataclasses import dataclass, replace
import re
from typing import Literal

from app.document_transform.ir import BlockKind, DocumentIR, SourceRange, utf16_length
from app.document_transform.native_chip_detection import DetectedNativeSpan
from app.document_transform.quotations import QUOTED_SPAN_PATTERN, quoted_block_ids
from app.identifiers import DocsTabId


_PROTECTED_INLINE = re.compile(r"(?P<code>`+[^`]+`+)|" + QUOTED_SPAN_PATTERN)


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


def protect_native_chip_candidate(
    candidate: DetectedNativeSpan,
    *,
    text: str,
    tab_id: DocsTabId,
    paragraph_start: int,
    protected: tuple[ProtectedNativeChipRange, ...],
    enabled: bool,
) -> DetectedNativeSpan:
    if not enabled:
        return candidate
    first, end = paragraph_start + candidate.start, paragraph_start + candidate.end
    for item in protected:
        if (
            item.source.tab_id == tab_id
            and item.source.segment_id is None
            and first < item.source.end_index
            and end > item.source.start_index
        ):
            return replace(candidate, skip_reason=item.reason)
    for match in _PROTECTED_INLINE.finditer(text):
        if candidate.start < utf16_length(text[: match.end()]) and candidate.end > utf16_length(
            text[: match.start()]
        ):
            return replace(
                candidate,
                skip_reason="protected_code"
                if match.group("code") is not None
                else "protected_quotation",
            )
    return candidate
