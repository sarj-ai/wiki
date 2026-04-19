"""Pure snippet-extraction helpers used by the Drive search service.

Separated from the service so they can be unit-tested without a user,
a Drive client, or a running event loop. Drive's full-text search stems
before matching (``phases`` → ``phase``); these helpers mirror that so
the snippet we show in the UI matches what Drive actually hit on.
"""

from __future__ import annotations

_SNIPPET_CONTEXT_CHARS = 120
_PREFIX_MIN_CHARS = 4


def escape_query_value(value: str) -> str:
    """Escape a raw user query for safe inclusion in a Drive ``q=`` string."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def extract_snippet(
    text: str,
    query: str,
    context_chars: int = _SNIPPET_CONTEXT_CHARS,
) -> tuple[str, str] | None:
    """Extract ~``context_chars`` of text around the first query match.

    Returns ``(snippet, matched_word)`` or ``None`` if nothing matched.
    Collapses runs of whitespace and prepends/appends an ellipsis when
    content was trimmed off.
    """
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
    return f"{prefix}{collapsed}{suffix}", word


def _first_match_offset(text: str, query: str) -> tuple[int, int, str] | None:
    if not query or not text:
        return None
    text_lower = text.lower()
    best: tuple[int, int, str] | None = None
    for word in query.split():
        w = word.strip().lower()
        if len(w) < 3:
            continue
        match = _find_with_prefix_fallback(text_lower, w)
        if match is None:
            continue
        idx, length = match
        if best is None or idx < best[0]:
            best = (idx, length, text[idx : idx + length])
    return best


def _find_with_prefix_fallback(text_lower: str, word: str) -> tuple[int, int] | None:
    # Drive's search stems words before matching, so the full query word
    # may not appear verbatim even when Drive reports a hit. Try the full
    # word first, then shorten one char at a time until we hit or fall
    # below the minimum-prefix threshold.
    candidate = word
    while len(candidate) >= _PREFIX_MIN_CHARS:
        idx = text_lower.find(candidate)
        if idx >= 0:
            return idx, len(candidate)
        candidate = candidate[:-1]
    return None
