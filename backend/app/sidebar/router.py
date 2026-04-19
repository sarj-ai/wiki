"""HTTP surface for the progressive sidebar loader.

Three endpoints, each independent so one slow drive doesn't gate the
whole sidebar:

1. ``GET /sidebar/drives`` — shared drives the user is in. Access-control
   boundary.
2. ``GET /sidebar/drives/{drive_id}/folders`` — folder tree for one drive
   (warm cache hit, cold cache walks Drive).
3. ``GET /sidebar/folders/{folder_id}/files`` — lazy file listing on folder
   expand, coalesced via singleflight.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.drive.dependencies import drive_service_dep
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


@router.get("/drives/{drive_id}/folders")
async def get_drive_folders(
    drive_id: str,
    db: AsyncSession = Depends(get_db),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> DriveTree:
    return await cache.get_drive_tree(db, drive_id)


@router.get("/folders/{folder_id}/files")
async def get_folder_files(
    folder_id: str,
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> list[DriveNode]:
    return await cache.get_folder_children(folder_id)
