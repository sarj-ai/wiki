from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.drive.dependencies import drive_service_dep, require_member_drive
from app.drive.schemas import DriveNode, DrivePermissionsPage, DriveSummary, DriveTree
from app.drive.service import DriveService
from app.identifiers import DriveId, FileId
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep


router = APIRouter(prefix="/sidebar", tags=["sidebar"])

_CACHE_CONTROL = "private, no-store"


@router.get("/drives")
async def get_drives(
    response: Response,
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> list[DriveSummary]:
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return await drive.list_drives()


@router.get(
    "/drives/{drive_id}/permissions",
    dependencies=[Depends(require_member_drive)],
)
async def get_drive_permissions(
    drive_id: DriveId,
    response: Response,
    drive: Annotated[DriveService, Depends(drive_service_dep)],
    page_token: str | None = None,
) -> DrivePermissionsPage:
    response.headers["Cache-Control"] = "private, no-store"
    return await drive.list_drive_permissions(drive_id, page_token=page_token)


@router.get(
    "/drives/{drive_id}/folders",
    response_model=DriveTree,
    dependencies=[Depends(require_member_drive)],
)
async def get_drive_folders(
    drive_id: DriveId,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> DriveTree | Response:
    cached = await cache.get_drive_tree(db, drive_id)
    etag = _tree_etag(drive_id, cached.refreshed_at)
    if etag is None:
        response.headers["Cache-Control"] = _CACHE_CONTROL
        return cached.tree
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL},
        )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return cached.tree


@router.post("/drives/{drive_id}/refresh", dependencies=[Depends(require_member_drive)])
async def refresh_drive_folders(
    drive_id: DriveId,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> DriveTree:
    return await cache.refresh_drive(db, drive_id)


@router.get("/folders/{folder_id}/files")
async def get_folder_files(
    folder_id: FileId,
    response: Response,
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> list[DriveNode]:
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return await cache.get_folder_children(folder_id)


def _tree_etag(drive_id: DriveId, refreshed_at: datetime | None) -> str | None:
    if refreshed_at is None:
        return None
    return f'W/"{drive_id}:{refreshed_at.isoformat()}"'
