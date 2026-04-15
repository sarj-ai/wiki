"""Drive Search API integration.

Scoped per-drive via ``corpora=drive`` + ``driveId=X``. Access control is
automatic — Drive only returns files the user's token is allowed to see.
The rev 4 spike confirmed full-text, name, mimeType, and date filters
all work and return in 800-1500ms at Sarj scale.

Since a user can be a member of multiple shared drives, the caller
typically fans out a query across every drive in ``drives.list()`` and
merges the results client-side. That fanout lives in ``app.sidebar``;
this module just handles one drive at a time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from googleapiclient.errors import HttpError
from pydantic import BaseModel

from app.drive.credentials import get_drive_service
from app.drive.schemas import SearchResult
from app.models.user import User

log = logging.getLogger(__name__)

# Google Workspace types that expose a clean text/plain export. Other
# types (PDF, images, Forms) either can't export as text or their text
# form isn't useful — we return a null snippet and let the frontend
# show a type-specific placeholder.
_TEXT_EXPORTABLE_MIME_TYPES = frozenset(
    {
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
    }
)

_SNIPPET_CONTEXT_CHARS = 120
_MAX_EXPORT_BYTES = 2 * 1024 * 1024  # 2 MB cap so huge docs don't stall search


class SnippetResult(BaseModel):
    """Snippet fetched from a file's plain-text export."""

    file_id: str
    mime_type: str
    snippet: str | None = None
    match_term: str | None = None


def _escape_query_value(value: str) -> str:
    """Escape a raw user query for safe inclusion in a Drive ``q=`` string.

    Drive's query language uses single-quoted string literals and
    backslash escaping. We mirror that to prevent a crafted query from
    breaking out of the literal and altering the filter semantics.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def search_drive(
    user: User,
    drive_id: str,
    query: str,
    *,
    limit: int = 50,
) -> list[SearchResult]:
    """Full-text + name search scoped to a single shared drive.

    Returns at most ``limit`` results, ordered by Drive's relevance
    ranking. Empty query strings short-circuit to an empty list so the
    caller doesn't accidentally fetch every file in the drive.
    """

    if not query.strip():
        return []

    escaped = _escape_query_value(query.strip())
    q = (
        f"(name contains '{escaped}' or fullText contains '{escaped}') "
        f"and trashed=false"
    )

    def _sync() -> list[SearchResult]:
        service = get_drive_service(user)
        params: dict[str, Any] = {
            "q": q,
            "corpora": "drive",
            "driveId": drive_id,
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "pageSize": limit,
            "fields": "files(id,name,mimeType,parents,modifiedTime,driveId)",
        }
        try:
            resp = service.files().list(**params).execute()
        except HttpError:
            log.exception(
                "search_drive failed user=%s drive=%s q=%r",
                user.id,
                drive_id,
                query,
            )
            return []
        out: list[SearchResult] = []
        for raw in resp.get("files", []):
            parents = raw.get("parents") or []
            parent_id = parents[0] if parents else None
            if parent_id == drive_id:
                parent_id = None
            out.append(
                SearchResult(
                    id=raw["id"],
                    name=raw["name"],
                    mime_type=raw["mimeType"],
                    drive_id=raw.get("driveId"),
                    parent_id=parent_id,
                    modified_time=raw.get("modifiedTime"),
                )
            )
        return out

    return await asyncio.to_thread(_sync)


_PREFIX_MIN_CHARS = 4


def _find_with_prefix_fallback(text_lower: str, word: str) -> tuple[int, int] | None:
    """Find ``word`` in ``text_lower`` with progressive prefix fallback.

    Drive's full-text search stems before matching — "phases" hits
    docs containing only "phase", and vice versa — so a naive
    substring search fails to find snippets for stemmed hits. We
    compensate by trying the full word first, then repeatedly
    stripping the trailing character down to ``_PREFIX_MIN_CHARS``
    until we hit. The first match wins, which is almost always the
    full form when the doc contains it and the stemmed form
    otherwise.

    Returns ``(offset, matched_length)`` or ``None``.
    """
    candidate = word
    while len(candidate) >= _PREFIX_MIN_CHARS:
        idx = text_lower.find(candidate)
        if idx >= 0:
            return idx, len(candidate)
        candidate = candidate[:-1]
    return None


def _first_match_offset(text: str, query: str) -> tuple[int, int, str] | None:
    """Find the first occurrence of any query word inside ``text``.

    Returns ``(offset, length, matched_word)`` or ``None`` if no match.
    Case-insensitive. Words shorter than 3 chars are skipped so common
    particles don't hijack the snippet selection — Drive's full-text
    search already guarantees at least one substantial word matched, so
    we won't miss the real hit.

    Each word is resolved via :func:`_find_with_prefix_fallback` so
    stemmed matches work the same way they do in Drive's own search
    ranking. The ``matched_word`` returned reflects the prefix that
    actually hit, which the frontend uses to highlight just the
    characters it found (otherwise we'd highlight "phases" when only
    "phase" exists in the text and the ``<mark>`` would miss).
    """
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


def extract_snippet(
    text: str,
    query: str,
    context_chars: int = _SNIPPET_CONTEXT_CHARS,
) -> tuple[str, str] | None:
    """Extract ~``context_chars`` of text around the first query match.

    Returns ``(snippet, matched_word)`` or ``None`` if nothing matched.
    Collapses runs of whitespace inside the snippet so exported Docs
    don't render as a noisy wall of blank characters, and prepends/
    appends an ellipsis when we trimmed off real content.
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


async def fetch_snippet(user: User, file_id: str, query: str) -> SnippetResult:
    """Fetch a plain-text export of ``file_id`` and extract a query snippet.

    Uses ``drive.files.export`` with ``mimeType='text/plain'``, which
    works for Docs / Sheets / Slides. Other types return a null snippet
    so the frontend can show a type-specific placeholder instead of
    pretending we have content.
    """

    def _sync() -> SnippetResult:
        service = get_drive_service(user)
        try:
            meta = (
                service.files()
                .get(fileId=file_id, fields="id,mimeType,size", supportsAllDrives=True)
                .execute()
            )
        except HttpError:
            log.exception("snippet metadata fetch failed file=%s", file_id)
            return SnippetResult(file_id=file_id, mime_type="", snippet=None)

        mime_type = meta.get("mimeType", "")
        if mime_type not in _TEXT_EXPORTABLE_MIME_TYPES:
            return SnippetResult(file_id=file_id, mime_type=mime_type, snippet=None)

        # ``size`` is only set on binary files in Drive — Workspace files
        # don't report a byte size, so we can't pre-filter by weight.
        # The export itself is the backstop: if the response is huge we
        # truncate before decoding.
        try:
            raw = service.files().export(fileId=file_id, mimeType="text/plain").execute()
        except HttpError:
            log.exception("snippet export failed file=%s", file_id)
            return SnippetResult(file_id=file_id, mime_type=mime_type, snippet=None)

        if isinstance(raw, bytes):
            raw = raw[:_MAX_EXPORT_BYTES]
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)[: _MAX_EXPORT_BYTES // 2]

        match = extract_snippet(text, query)
        if match is None:
            return SnippetResult(file_id=file_id, mime_type=mime_type, snippet=None)
        snippet, word = match
        return SnippetResult(
            file_id=file_id,
            mime_type=mime_type,
            snippet=snippet,
            match_term=word,
        )

    return await asyncio.to_thread(_sync)


async def search_across_drives(
    user: User,
    drive_ids: list[str],
    query: str,
    *,
    limit_per_drive: int = 20,
) -> list[SearchResult]:
    """Fan-out search across multiple shared drives in parallel.

    Each drive's search runs in its own ``asyncio.to_thread`` call so
    the total wall time is the slowest single drive, not the sum.
    Results preserve per-drive ordering and are concatenated in the same
    order as ``drive_ids``.
    """
    if not drive_ids or not query.strip():
        return []

    tasks = [
        search_drive(user, drive_id, query, limit=limit_per_drive) for drive_id in drive_ids
    ]
    results = await asyncio.gather(*tasks)
    merged: list[SearchResult] = []
    for per_drive in results:
        merged.extend(per_drive)
    return merged
