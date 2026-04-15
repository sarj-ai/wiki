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

from app.drive.credentials import get_drive_service
from app.drive.schemas import SearchResult
from app.models.user import User

log = logging.getLogger(__name__)


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
