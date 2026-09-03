from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.drive.dependencies import drive_service_dep
from app.drive.schemas import SearchResult, SnippetResult
from app.drive.service import DriveService
from app.identifiers import FileId
from app.openapi import authentication_responses


router = APIRouter(
    prefix="/drive-search",
    tags=["drive-search"],
    responses=authentication_responses(),
)


@router.get("", status_code=status.HTTP_200_OK, operation_id="search_drive")
async def search(
    q: Annotated[str, Query(min_length=1, max_length=2048)],
    svc: Annotated[DriveService, Depends(drive_service_dep)],
    limit_per_drive: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SearchResult]:
    return await svc.search(q, limit_per_drive=limit_per_drive)


@router.get(
    "/snippet",
    status_code=status.HTTP_200_OK,
    operation_id="get_search_snippet",
)
async def snippet(
    file_id: Annotated[FileId, Query(min_length=1)],
    q: Annotated[str, Query(min_length=1, max_length=2048)],
    svc: Annotated[DriveService, Depends(drive_service_dep)],
    mime_type: Annotated[str | None, Query(max_length=256)] = None,
) -> SnippetResult:
    return await svc.fetch_snippet(file_id, q, mime_type=mime_type)
