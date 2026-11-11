from bisect import bisect_left, bisect_right
import re
from types import MappingProxyType

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
_CODE_TOKEN = re.compile(r"`+|\n")
_CLOSING_QUOTE = MappingProxyType({'"': '"', "'": "'", "“": "”", "‘": "’"})
_OPENING_QUOTE = MappingProxyType({closing: opening for opening, closing in _CLOSING_QUOTE.items()})


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
    masked = _mask_inline_code(source)
    starts = []
    ends = []
    cursor = 0
    for text in texts:
        starts.append(cursor)
        ends.append(cursor + len(text))
        cursor += len(text) + 1
    protected: set[int] = set()
    openings: dict[str, int] = {}
    covered_until: dict[str, int] = {}
    # Scan once: repeated unmatched opening marks in a large document must not
    # cause a regular expression to rescan the remainder at every position.
    for token in _QUOTE_TOKEN.finditer(masked):
        mark, position = token.group(), token.start()
        previous_word = position > 0 and _word_character(masked[position - 1])
        next_word = token.end() < len(masked) and _word_character(masked[token.end()])
        if mark in {"'", "’"} and previous_word and next_word:
            continue
        opener = _OPENING_QUOTE.get(mark)
        if opener is not None and opener in openings and not (mark == "'" and next_word):
            first = bisect_right(ends, openings[opener])
            stop = bisect_left(starts, token.end())
            if stop - first > 1:
                protected.update(range(max(first, covered_until.get(opener, first)), stop))
                covered_until[opener] = stop
            # A plural possessive can also look like a closing mark. Protect
            # that possible span, but retain its opener for a later close.
            if not _possible_plural_possessive(masked, mark, position):
                openings.pop(opener)
        elif mark in _CLOSING_QUOTE and not (mark == "'" and previous_word):
            openings[mark] = position
    return frozenset(protected)


def _possible_plural_possessive(source: str, mark: str, position: int) -> bool:
    return mark in {"'", "’"} and position > 0 and source[position - 1] in {"s", "S"}


def _mask_inline_code(source: str) -> str:
    tokens = list(_CODE_TOKEN.finditer(source))
    next_delimiter: dict[int, int] = {}
    partners: dict[int, int] = {}
    for index in range(len(tokens) - 1, -1, -1):
        delimiter = tokens[index].group()
        if delimiter == "\n":
            next_delimiter.clear()
        else:
            length = len(delimiter)
            if length in next_delimiter:
                partners[index] = next_delimiter[length]
            next_delimiter[length] = index
    parts: list[str] = []
    cursor = index = 0
    while index < len(tokens):
        partner = partners.get(index)
        if partner is None:
            index += 1
            continue
        start, end = tokens[index].start(), tokens[partner].end()
        parts.extend((source[cursor:start], " " * (end - start)))
        cursor, index = end, partner + 1
    return "".join((*parts, source[cursor:]))


def _word_character(value: str) -> bool:
    return value.isalnum() or value == "_"
