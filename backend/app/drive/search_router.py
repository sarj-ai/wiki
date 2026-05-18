"""HTTP surface for Drive Search.

Fans out per-drive Search API calls across every shared drive the user
is a member of; Drive's own ACL filters results so we never need to
post-filter.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.drive.dependencies import drive_service_dep
from app.drive.schemas import SearchResult, SnippetResult
from app.drive.service import DriveService

router = APIRouter(prefix="/drive-search", tags=["drive-search"])


@router.get("")
async def search(
    q: str = Query(..., min_length=1),
    limit_per_drive: int = Query(20, ge=1, le=100),
    svc: DriveService = Depends(drive_service_dep),
) -> list[SearchResult]:
    return await svc.search(q, limit_per_drive=limit_per_drive)


@router.get("/snippet")
async def snippet(
    file_id: str = Query(..., min_length=1),
    q: str = Query(..., min_length=1),
    svc: DriveService = Depends(drive_service_dep),
) -> SnippetResult:
    return await svc.fetch_snippet(file_id, q)
