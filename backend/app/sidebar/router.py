from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.drive.dependencies import drive_service_dep, require_member_drive
from app.drive.schemas import DriveNode, DriveSummary, DriveTree
from app.drive.service import DriveService
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep


router = APIRouter(prefix="/sidebar", tags=["sidebar"])


@router.get("/drives")
async def get_drives(
    drive: DriveService = Depends(drive_service_dep),
) -> list[DriveSummary]:
    return await drive.list_drives()


@router.get(
    "/drives/{drive_id}/folders",
    response_model=DriveTree,
    dependencies=[Depends(require_member_drive)],
)
async def get_drive_folders(
    drive_id: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> DriveTree | Response:
    cached = await cache.get_drive_tree(db, drive_id)
    etag = _tree_etag(drive_id, cached.refreshed_at)
    if etag is None:
        return cached.tree
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    return cached.tree


@router.post("/drives/{drive_id}/refresh", dependencies=[Depends(require_member_drive)])
async def refresh_drive_folders(
    drive_id: str,
    db: AsyncSession = Depends(get_db),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> DriveTree:
    return await cache.refresh_drive(db, drive_id)


@router.get("/folders/{folder_id}/files")
async def get_folder_files(
    folder_id: str,
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> list[DriveNode]:
    return await cache.get_folder_children(folder_id)


def _tree_etag(drive_id: str, refreshed_at: datetime | None) -> str | None:
    if refreshed_at is None:
        return None
    return f'W/"{drive_id}:{refreshed_at.isoformat()}"'
