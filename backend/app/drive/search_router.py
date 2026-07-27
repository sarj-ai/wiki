from __future__ import annotations

from typing import Annotated

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
    request: Request,  # ruff: ignore[unused-function-argument] — slowapi reads the client IP off this
    q: Annotated[str, Query(min_length=1, max_length=2048)],
    svc: Annotated[DriveService, Depends(drive_service_dep)],
    limit_per_drive: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SearchResult]:
    return await svc.search(q, limit_per_drive=limit_per_drive)


@router.get("/snippet")
@limiter.limit(_SEARCH_LIMIT)  # pyright: ignore[reportUntypedFunctionDecorator]
async def snippet(
    request: Request,  # ruff: ignore[unused-function-argument] — slowapi reads the client IP off this
    file_id: Annotated[str, Query(min_length=1)],
    q: Annotated[str, Query(min_length=1, max_length=2048)],
    svc: Annotated[DriveService, Depends(drive_service_dep)],
    mime_type: Annotated[str | None, Query(max_length=256)] = None,
) -> SnippetResult:
    return await svc.fetch_snippet(file_id, q, mime_type=mime_type)
