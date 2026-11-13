from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import StrEnum
import re
from types import MappingProxyType
from typing import ClassVar, Literal, assert_never

from app.document_transform.inline_code import mask_inline_code
from app.document_transform.ir import BlockKind, DocumentIR
from app.identifiers import DocsSegmentId, DocsTabId


# Apostrophes within words belong to the quotation. A quotation may continue
# across line or paragraph breaks; those breaks do not permit paraphrasing it.
SINGLE_QUOTED_SPAN_PATTERN = (
    r"(?<!\w)'(?:[^']|(?<=\w)'(?=\w)|(?<=[sS])'(?!\w))+'(?!\w)|"
    r"‘(?:[^‘’]|(?<=\w)’(?=\w)|(?<=[sS])’(?!\w))+’"
)
QUOTED_SPAN_PATTERN = rf'"[^"]+"|“[^”]+”|{SINGLE_QUOTED_SPAN_PATTERN}'
_QUOTE_TOKEN = re.compile(r"""["“”‘’']""")


class QuoteDelimiter(StrEnum):
    STRAIGHT_DOUBLE = '"'
    STRAIGHT_SINGLE = "'"
    CURLY_DOUBLE = "“"
    CURLY_SINGLE = "‘"


_CLOSING_QUOTE = MappingProxyType(
    {
        QuoteDelimiter.STRAIGHT_DOUBLE: '"',
        QuoteDelimiter.STRAIGHT_SINGLE: "'",
        QuoteDelimiter.CURLY_DOUBLE: "”",
        QuoteDelimiter.CURLY_SINGLE: "’",
    }
)
_OPENING_QUOTE = MappingProxyType({closing: opening for opening, closing in _CLOSING_QUOTE.items()})


@dataclass(frozen=True, slots=True)
class EmptyQuotation:
    kind: ClassVar[Literal["empty"]] = "empty"
    start: int


@dataclass(frozen=True, slots=True)
class ContinuingQuotation:
    kind: ClassVar[Literal["continuing"]] = "continuing"
    start: int
    last_paragraph: int


type OpenQuotation = EmptyQuotation | ContinuingQuotation


@dataclass(frozen=True, slots=True)
class _ParagraphPositions:
    starts: list[int]
    ends: list[int]
    openings: set[int]
    last_text: list[int]


@dataclass(frozen=True, slots=True)
class _QuotationState:
    quotes: dict[QuoteDelimiter, OpenQuotation]
    covered_until: dict[QuoteDelimiter, int]
    protected: set[int]


def quoted_block_ids(document: DocumentIR) -> frozenset[str]:
    groups: dict[tuple[DocsTabId | None, DocsSegmentId | None], list[int]] = {}
    for index, block in enumerate(document.blocks):
        source = block.source_range
        scope = (source.tab_id, source.segment_id) if source is not None else (None, None)
        groups.setdefault(scope, []).append(index)
    protected: set[str] = set()
    for indices in groups.values():
        texts = tuple(
            " " * len(document.blocks[index].text)
            if document.blocks[index].kind in {BlockKind.CODE, BlockKind.TABLE, BlockKind.QUOTE}
            else document.blocks[index].text
            for index in indices
        )
        protected.update(
            document.blocks[indices[index]].id for index in _multiblock_quotation_indices(texts)
        )
    return frozenset(protected)


def _multiblock_quotation_indices(texts: tuple[str, ...]) -> frozenset[int]:
    source = "\n".join(texts)
    # Code delimiters and string literals must not open or close prose quotes.
    masked = mask_inline_code(source)
    positions = _paragraph_positions(texts)
    state = _QuotationState(quotes={}, covered_until={}, protected=set())
    # Scan once: repeated unmatched opening marks in a large document must not
    # cause a regular expression to rescan the remainder at every position.
    for token in _QUOTE_TOKEN.finditer(masked):
        mark, position = token.group(), token.start()
        previous_word = position > 0 and _word_character(masked[position - 1])
        next_word = token.end() < len(masked) and _word_character(masked[token.end()])
        if mark in {"'", "’"} and previous_word and next_word:
            continue
        opener = _OPENING_QUOTE.get(mark)
        if opener is not None and opener in state.quotes and not (mark == "'" and next_word):
            _close_quotation(state, positions, masked, token)
        elif mark in _CLOSING_QUOTE and not (mark == "'" and previous_word):
            _open_quotation(state, positions, token)
    return frozenset(state.protected)


def _paragraph_positions(texts: tuple[str, ...]) -> _ParagraphPositions:
    starts = []
    ends = []
    openings: set[int] = set()
    last_text = []
    cursor = 0
    for text in texts:
        starts.append(cursor)
        ends.append(cursor + len(text))
        openings.add(cursor + len(text) - len(text.lstrip()))
        last_text.append(cursor + len(text.rstrip()) - 1)
        cursor += len(text) + 1
    return _ParagraphPositions(starts, ends, openings, last_text)


def _close_quotation(
    state: _QuotationState, positions: _ParagraphPositions, source: str, token: re.Match[str]
) -> None:
    opener = _OPENING_QUOTE[token.group()]
    first = bisect_right(positions.ends, state.quotes[opener].start)
    stop = bisect_left(positions.starts, token.end())
    if stop - first > 1:
        state.protected.update(range(max(first, state.covered_until.get(opener, first)), stop))
        state.covered_until[opener] = stop
    # A plural possessive can also look like a closing mark. Retain both its
    # opener and continuation state until a definite closing mark arrives.
    if not _possible_plural_possessive(source, token.group(), token.start()):
        state.quotes.pop(opener)


def _open_quotation(
    state: _QuotationState, positions: _ParagraphPositions, token: re.Match[str]
) -> None:
    delimiter = QuoteDelimiter(token.group())
    state.quotes[delimiter] = opening_quotation(state.quotes.get(delimiter), positions, token)


def opening_quotation(
    current: OpenQuotation | None, positions: _ParagraphPositions, token: re.Match[str]
) -> OpenQuotation:
    position = token.start()
    paragraph = bisect_right(positions.starts, position) - 1
    match current:
        case ContinuingQuotation(start, previous) if (
            position in positions.openings
            and previous == _previous_text_block(positions, paragraph)
        ):
            return ContinuingQuotation(start, paragraph)
        case EmptyQuotation() | ContinuingQuotation() | None:
            # A later unrelated opener starts a new quotation. Empty unmatched
            # openers cannot establish continuation across paragraph boundaries.
            return (
                ContinuingQuotation(position, paragraph)
                if position < positions.last_text[paragraph]
                else EmptyQuotation(position)
            )
        case _:
            assert_never(current)


def _previous_text_block(positions: _ParagraphPositions, paragraph: int) -> int:
    paragraph -= 1
    while paragraph >= 0 and positions.last_text[paragraph] < positions.starts[paragraph]:
        paragraph -= 1
    return paragraph


def _possible_plural_possessive(source: str, mark: str, position: int) -> bool:
    return mark in {"'", "’"} and position > 0 and source[position - 1] in {"s", "S"}


def _word_character(value: str) -> bool:
    return value.isalnum() or value == "_"
