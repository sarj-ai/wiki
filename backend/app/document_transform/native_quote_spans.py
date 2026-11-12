from dataclasses import dataclass, field
import re
from types import MappingProxyType
from typing import Literal

from app.document_transform.ir import utf16_length


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


@dataclass(slots=True)
class _InlineState:
    openings: dict[str, int] = field(default_factory=dict)
    code: tuple[str, int] | None = None
    ranges: list[NativeInlineRange] = field(default_factory=list)

    def accept(self, mark: str, start: int, end: int, *, previous: str, following: str) -> None:
        if mark.startswith("`"):
            self._code_token(mark, start, end)
            return
        if self.code is not None:
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
        if self.code is None:
            self.code = mark, start
        elif self.code[0] == mark:
            self.ranges.append(NativeInlineRange(self.code[1], end, "protected_code"))
            self.code = None

    def finish(self, end: int) -> tuple[NativeInlineRange, ...]:
        result = list(self.ranges)
        result.extend(
            NativeInlineRange(start, end, "unclosed_quotation") for start in self.openings.values()
        )
        if self.code is not None:
            result.append(NativeInlineRange(self.code[1], end, "unclosed_code"))
        return tuple(result)


def _word(value: str) -> bool:
    return value.isalnum() or value == "_"


def native_inline_protected_ranges(text: str) -> tuple[NativeInlineRange, ...]:
    # Scan tokens once and accumulate UTF-16 offsets once. Repeated unmatched
    # opening marks must not rescan the remaining document for every candidate.
    state = _InlineState()
    cursor = offset = 0
    for token in _TOKENS.finditer(text):
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
