from __future__ import annotations

from typing import NamedTuple


_SNIPPET_CONTEXT_CHARS = 120
_PREFIX_MIN_CHARS = 4


class Snippet(NamedTuple):
    text: str
    matched_word: str


class _MatchOffset(NamedTuple):
    offset: int
    length: int
    word: str


class _MatchPosition(NamedTuple):
    offset: int
    length: int


def escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def extract_snippet(
    text: str,
    query: str,
    context_chars: int = _SNIPPET_CONTEXT_CHARS,
) -> Snippet | None:
    match = _first_match_offset(text, query)
    if match is None:
        return None
    offset, length, word = match
    start = max(0, offset - context_chars)
    end = min(len(text), offset + length + context_chars)
    window = text[start:end]
    collapsed = " ".join(window.split())
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return Snippet(f"{prefix}{collapsed}{suffix}", word)


def _first_match_offset(text: str, query: str) -> _MatchOffset | None:
    if not query or not text:
        return None
    text_lower = text.lower()
    best: _MatchOffset | None = None
    for word in query.split():
        w = word.strip().lower()
        if len(w) < 3:
            continue
        match = _find_with_prefix_fallback(text_lower, w)
        if match is None:
            continue
        idx, length = match
        if best is None or idx < best[0]:
            best = _MatchOffset(idx, length, text[idx : idx + length])
    return best


def _find_with_prefix_fallback(text_lower: str, word: str) -> _MatchPosition | None:
    candidate = word
    while len(candidate) >= _PREFIX_MIN_CHARS:
        idx = text_lower.find(candidate)
        if idx >= 0:
            return _MatchPosition(idx, len(candidate))
        candidate = candidate[:-1]
    return None
