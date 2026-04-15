"""HTTP surface for Drive Search API (replaces the old tsvector path).

Mounted at ``/drive-search`` rather than ``/search`` only to avoid
colliding with the legacy tsvector router during the pivot. Sub-PR
C5 deletes the legacy router and this one can move to ``/search``
without changing any frontend calls.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import get_current_user
from app.drive.drives import list_user_drives
from app.drive.schemas import SearchResult
from app.drive.search import SnippetResult, fetch_snippet, search_across_drives
from app.models.user import User

router = APIRouter(prefix="/drive-search", tags=["drive-search"])


@router.get("")
async def search(
    q: str = Query(..., min_length=1),
    limit_per_drive: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
) -> list[SearchResult]:
    """Fan out a Drive Search API call across every accessible drive.

    Access control is automatic — Drive only returns files the
    caller's OAuth token can see. Scoping per drive (via
    ``corpora=drive&driveId=...``) limits results to Sarj's shared
    drives and keeps users' personal ``My Drive`` out of the results.
    """
    drives = await list_user_drives(user)
    if not drives:
        return []
    return await search_across_drives(
        user,
        [d.id for d in drives],
        q,
        limit_per_drive=limit_per_drive,
    )


@router.get("/snippet")
async def snippet(
    file_id: str = Query(..., min_length=1),
    q: str = Query(..., min_length=1),
    user: User = Depends(get_current_user),
) -> SnippetResult:
    """Return a short text excerpt around the first query match in a file.

    Fetched lazily by the command palette as the user highlights each
    result — only one snippet is loaded at a time, so an expensive
    text/plain export only runs when the user actually needs to see
    the match context. Non-text types (PDF, images, forms) come back
    with ``snippet=None`` and the frontend shows a type-specific
    placeholder instead.
    """
    return await fetch_snippet(user, file_id, q)
