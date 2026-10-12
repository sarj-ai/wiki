from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.drive.dependencies import drive_service_dep, require_member_drive
from app.drive.schemas import DriveNode, DrivePermissionsPage, DriveSummary, DriveTree
from app.drive.service import DriveService
from app.identifiers import DriveId, FileId
from app.openapi import authentication_responses
from app.sidebar.cache import SidebarCacheService, tree_etag as _tree_etag
from app.sidebar.dependencies import sidebar_cache_service_dep


router = APIRouter(prefix="/sidebar", tags=["sidebar"], responses=authentication_responses())

_CACHE_CONTROL = "private, no-store"


@router.get(
    "/drives",
    status_code=status.HTTP_200_OK,
    operation_id="list_drives",
)
async def get_drives(
    response: Response,
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> list[DriveSummary]:
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return await drive.list_drives()


@router.get(
    "/drives/{drive_id}/permissions",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_member_drive)],
    operation_id="list_drive_permissions",
)
async def get_drive_permissions(
    drive_id: Annotated[DriveId, Path(min_length=1)],
    response: Response,
    drive: Annotated[DriveService, Depends(drive_service_dep)],
    page_token: Annotated[str | None, Query(max_length=4096)] = None,
) -> DrivePermissionsPage:
    response.headers["Cache-Control"] = "private, no-store"
    return await drive.list_drive_permissions(drive_id, page_token=page_token)


@router.get(
    "/drives/{drive_id}/folders",
    status_code=status.HTTP_200_OK,
    response_model=DriveTree,
    dependencies=[Depends(require_member_drive)],
    operation_id="get_drive_tree",
    responses={
        304: {
            "description": "The cached tree matches If-None-Match and has no response body.",
            "headers": {
                "ETag": {"schema": {"type": "string"}},
                "Cache-Control": {"schema": {"type": "string"}},
            },
        }
    },
)
async def get_drive_folders(
    drive_id: Annotated[DriveId, Path(min_length=1)],
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> DriveTree | Response:
    request_etag = request.headers.get("if-none-match")
    if request_etag:
        conditional = await cache.get_drive_tree_if_modified(
            db, drive_id, request_etag=request_etag
        )
        metadata_etag = _tree_etag(drive_id, conditional.refreshed_at)
        if conditional.cached is None and metadata_etag is not None:
            return Response(
                status_code=status.HTTP_304_NOT_MODIFIED,
                headers={"ETag": metadata_etag, "Cache-Control": _CACHE_CONTROL},
            )
        cached = conditional.cached
        if cached is None:
            msg = "Conditional tree response lacks a tree or timestamp"
            raise RuntimeError(msg)
    else:
        cached = await cache.get_drive_tree(db, drive_id)
    etag = _tree_etag(drive_id, cached.refreshed_at)
    if etag is None:
        response.headers["Cache-Control"] = _CACHE_CONTROL
        return cached.tree
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return cached.tree


@router.post(
    "/drives/{drive_id}/refresh",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_member_drive)],
    operation_id="refresh_drive_tree",
)
async def refresh_drive_folders(
    drive_id: Annotated[DriveId, Path(min_length=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> DriveTree:
    return await cache.refresh_drive(db, drive_id)


@router.get(
    "/folders/{folder_id}/files",
    status_code=status.HTTP_200_OK,
    operation_id="list_folder_files",
)
async def get_folder_files(
    folder_id: Annotated[FileId, Path(min_length=1)],
    response: Response,
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> list[DriveNode]:
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return await cache.get_folder_children(folder_id)
