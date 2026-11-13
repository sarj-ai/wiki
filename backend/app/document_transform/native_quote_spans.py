from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import ClassVar, Literal, assert_never

from app.document_transform.inline_code import mask_inline_code
from app.document_transform.ir import utf16_length
from app.document_transform.quantity_notation import mask_unit_annotation_marks
from app.document_transform.quotations import quotation_scan_sources


type ProtectedInlineReason = Literal[
    "protected_quotation", "protected_code", "unclosed_quotation", "unclosed_code"
]
_TOKENS = re.compile(r"`+|[\"“”‘’']")
_OPENERS = MappingProxyType({'"': '"', "'": "'", "“": "”", "‘": "’"})
_CLOSERS = MappingProxyType({end: start for start, end in _OPENERS.items()})


@dataclass(frozen=True, slots=True)
class NativeInlineRange:
    start: int
    end: int
    reason: ProtectedInlineReason


@dataclass(frozen=True, slots=True)
class OutsideCode:
    kind: ClassVar[Literal["outside-code"]] = "outside-code"


@dataclass(frozen=True, slots=True)
class InsideCode:
    kind: ClassVar[Literal["inside-code"]] = "inside-code"
    delimiter: str
    start: int

    def __post_init__(self) -> None:
        if not self.delimiter or self.delimiter.strip("`") or self.start < 0:
            msg = "Invalid open inline code state"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CodeTransition:
    state: OutsideCode | InsideCode
    ranges: tuple[NativeInlineRange, ...]


def advance_code(
    state: OutsideCode | InsideCode, mark: str, start: int, end: int
) -> CodeTransition:
    if not mark or mark.strip("`") or start < 0 or end <= start:
        msg = "Invalid inline code token or UTF-16 range"
        raise ValueError(msg)
    if isinstance(state, InsideCode) and start < state.start + len(state.delimiter):
        msg = "Inline code events must follow the opening delimiter"
        raise ValueError(msg)
    match state:
        case OutsideCode():
            return CodeTransition(InsideCode(mark, start), ())
        case InsideCode(delimiter, opened) if delimiter == mark:
            return CodeTransition(
                OutsideCode(), (NativeInlineRange(opened, end, "protected_code"),)
            )
        case InsideCode():
            return CodeTransition(state, ())
        case _:
            assert_never(state)


@dataclass(slots=True)
class _InlineState:
    openings: dict[str, int] = field(default_factory=dict)
    code: OutsideCode | InsideCode = field(default_factory=OutsideCode)
    ranges: list[NativeInlineRange] = field(default_factory=list)

    def accept(self, mark: str, start: int, end: int, *, previous: str, following: str) -> None:
        if mark.startswith("`"):
            self._code_token(mark, start, end)
            return
        if isinstance(self.code, InsideCode):
            return
        if mark in {"'", "’"} and _word(previous) and _word(following):
            return
        opener = _CLOSERS.get(mark)
        if (
            opener is not None
            and opener in self.openings
            and not (mark == "'" and _word(following))
        ):
            self.ranges.append(NativeInlineRange(self.openings[opener], end, "protected_quotation"))
            # A plural possessive may also look like a closing quote. Retain
            # the opener until a definite close so ambiguity cannot expose text.
            if not (mark in {"'", "’"} and previous in {"s", "S"}):
                self.openings.pop(opener)
        elif mark in _OPENERS and not (mark == "'" and _word(previous)):
            self.openings.setdefault(mark, start)

    def _code_token(self, mark: str, start: int, end: int) -> None:
        transition = advance_code(self.code, mark, start, end)
        self.code = transition.state
        self.ranges.extend(transition.ranges)

    def finish(self, end: int) -> tuple[NativeInlineRange, ...]:
        result = list(self.ranges)
        result.extend(
            NativeInlineRange(start, end, "unclosed_quotation") for start in self.openings.values()
        )
        if isinstance(self.code, InsideCode):
            result.append(NativeInlineRange(self.code.start, end, "unclosed_code"))
        return tuple(result)


def _word(value: str) -> bool:
    return value.isalnum() or value == "_"


def native_inline_protected_ranges(text: str) -> tuple[NativeInlineRange, ...]:
    primary = _scan_inline_ranges(text, mask_unit_annotation_marks(text))
    if len(quotation_scan_sources(mask_inline_code(text))) == 1:
        return primary
    original_quotes = tuple(
        span for span in _scan_inline_ranges(text, text) if span.reason == "protected_quotation"
    )
    # An original matched opener can retain its original closing interpretation.
    # A raw closing mark may instead open a genuine later unfinished quotation;
    # keep that primary protection even if it conservatively covers later text.
    original_openings = {span.start for span in original_quotes}
    retained = tuple(
        span
        for span in primary
        if span.reason != "unclosed_quotation" or span.start not in original_openings
    )
    return tuple(
        sorted({*retained, *original_quotes}, key=lambda span: (span.start, span.end, span.reason))
    )


def _scan_inline_ranges(text: str, source: str) -> tuple[NativeInlineRange, ...]:
    # Scan tokens once and accumulate UTF-16 offsets once. Repeated unmatched
    # opening marks must not rescan the remaining document for every candidate.
    state = _InlineState()
    cursor = offset = 0
    for token in _TOKENS.finditer(source):
        offset += utf16_length(text[cursor : token.start()])
        start = offset
        offset += utf16_length(token.group())
        state.accept(
            token.group(),
            start,
            offset,
            previous=text[token.start() - 1] if token.start() else "",
            following=text[token.end()] if token.end() < len(text) else "",
        )
        cursor = token.end()
    return state.finish(utf16_length(text))
