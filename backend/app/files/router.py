"""HTTP surface for Navigator file CRUD.

Every endpoint invalidates the affected drive's sidebar cache inline
so the next sidebar load reflects the write.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.files.dependencies import files_service_dep
from app.files.schemas import FileCopy, FileCreate, FileMove, FileRename, FileResponse
from app.files.service import FilesService
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep

router = APIRouter(prefix="/files", tags=["files"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_file(
    payload: FileCreate,
    db: AsyncSession = Depends(get_db),
    files: FilesService = Depends(files_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> FileResponse:
    result = await files.create(payload.parent_folder_id, payload.name, payload.mime_type)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.get("/{file_id}")
async def read_file(
    file_id: str,
    files: FilesService = Depends(files_service_dep),
) -> FileResponse:
    return await files.get(file_id)


@router.patch("/{file_id}/rename")
async def rename_file(
    file_id: str,
    payload: FileRename,
    db: AsyncSession = Depends(get_db),
    files: FilesService = Depends(files_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> FileResponse:
    result = await files.rename(file_id, payload.name)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.patch("/{file_id}/move")
async def move_file(
    file_id: str,
    payload: FileMove,
    db: AsyncSession = Depends(get_db),
    files: FilesService = Depends(files_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> FileResponse:
    # Invalidate the source drive BEFORE the move — afterward we only
    # know the destination.
    source = await files.get(file_id)
    await _invalidate_drive_of(db, cache, source)

    result = await files.move(file_id, payload.new_parent_folder_id)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.post("/{file_id}/copy", status_code=status.HTTP_201_CREATED)
async def copy_file(
    file_id: str,
    payload: FileCopy,
    db: AsyncSession = Depends(get_db),
    files: FilesService = Depends(files_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> FileResponse:
    result = await files.copy(file_id, payload.name)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    files: FilesService = Depends(files_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> None:
    source = await files.get(file_id)
    await files.trash(file_id)
    await _invalidate_drive_of(db, cache, source)


async def _invalidate_drive_of(
    db: AsyncSession,
    cache: SidebarCacheService,
    file: FileResponse,
) -> None:
    # ``driveId`` is set for every shared-drive file; a missing one means
    # My Drive, which we don't cache.
    if file.drive_id:
        await cache.invalidate_drive(db, file.drive_id)
