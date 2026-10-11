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


def has_snippet_terms(query: str) -> bool:
    return bool(_snippet_terms(query))


def _snippet_terms(query: str) -> dict[str, None]:
    return dict.fromkeys(word for word in query.lower().split() if len(word) >= _PREFIX_MIN_CHARS)


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
    words = _snippet_terms(query)
    if not words:
        return None
    text_lower = text.lower()
    best: _MatchOffset | None = None
    for w in words:
        match = _find_with_prefix_fallback(text_lower, w)
        if match is None:
            continue
        idx, length = match
        if idx == 0:
            return _MatchOffset(idx, length, text[idx : idx + length])
        if best is None or idx < best[0]:
            best = _MatchOffset(idx, length, text[idx : idx + length])
    return best


def _find_with_prefix_fallback(text_lower: str, word: str) -> _MatchPosition | None:
    if len(word) < _PREFIX_MIN_CHARS:
        return None
    exact = text_lower.find(word)
    if exact >= 0:
        return _MatchPosition(exact, len(word))
    if len(word) == _PREFIX_MIN_CHARS:
        return None
    best_offset = text_lower.find(word[:_PREFIX_MIN_CHARS])
    if best_offset < 0:
        return None
    best_length = _PREFIX_MIN_CHARS
    lower = best_length + 1
    upper = len(word) - 1
    # Every matching prefix implies that all shorter prefixes also match.
    # Find the longest match without rescanning for each missing suffix.
    while lower <= upper:
        length = (lower + upper) // 2
        offset = text_lower.find(word[:length])
        if offset >= 0:
            best_offset = offset
            best_length = length
            lower = length + 1
        else:
            upper = length - 1
    return _MatchPosition(best_offset, best_length)
