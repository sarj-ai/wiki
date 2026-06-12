from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.drive.dependencies import drive_service_dep
from app.drive.schemas import SearchResult, SnippetResult
from app.drive.service import DriveService
from app.ratelimit import limiter


router = APIRouter(prefix="/drive-search", tags=["drive-search"])

_SEARCH_LIMIT = "30/minute"


@router.get("")
@limiter.limit(_SEARCH_LIMIT)  # pyright: ignore[reportUntypedFunctionDecorator]
async def search(
    request: Request,  # noqa: ARG001 — slowapi reads the client IP off this
    q: str = Query(..., min_length=1),
    limit_per_drive: int = Query(20, ge=1, le=100),
    svc: DriveService = Depends(drive_service_dep),
) -> list[SearchResult]:
    return await svc.search(q, limit_per_drive=limit_per_drive)


@router.get("/snippet")
@limiter.limit(_SEARCH_LIMIT)  # pyright: ignore[reportUntypedFunctionDecorator]
async def snippet(
    request: Request,  # noqa: ARG001 — slowapi reads the client IP off this
    file_id: str = Query(..., min_length=1),
    q: str = Query(..., min_length=1),
    mime_type: str | None = Query(None, max_length=256),
    svc: DriveService = Depends(drive_service_dep),
) -> SnippetResult:
    return await svc.fetch_snippet(file_id, q, mime_type=mime_type)
