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

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, status
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


@router.get("/drives/{drive_id}/folders", response_model=DriveTree)
async def get_drive_folders(
    drive_id: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> DriveTree | Response:
    # The cache row's ``refreshed_at`` is a free change validator: same
    # stamp ⇒ byte-identical tree. A matching ``If-None-Match`` turns a
    # large JSON response into an empty 304. Weak tag because the gzip
    # middleware may vary the bytes; ``no-cache`` means "store, but
    # revalidate" — exactly the ETag round-trip we want from browsers.
    cached = await cache.get_drive_tree(db, drive_id)
    etag = _tree_etag(drive_id, cached.refreshed_at)
    if etag is None:
        return cached.tree
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "private, no-cache"
    return cached.tree


@router.post("/drives/{drive_id}/refresh")
async def refresh_drive_folders(
    drive_id: str,
    db: AsyncSession = Depends(get_db),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> DriveTree:
    """Force-refresh a drive's tree, bypassing the stale-while-revalidate
    window. Returns the freshly-walked tree so the client gets it in one
    round-trip instead of having to refetch separately.
    """
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
