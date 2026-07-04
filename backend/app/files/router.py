from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.extras.dependencies import extras_service_dep
from app.extras.service import ExtrasService
from app.files.breadcrumb import build_breadcrumb
from app.files.dependencies import files_service_dep
from app.files.schemas import (
    DocComment,
    DocHtml,
    FileCopy,
    FileCreate,
    FileDetail,
    FileMove,
    FileRename,
    FileResponse,
)
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
    db: AsyncSession = Depends(get_db),
    files: FilesService = Depends(files_service_dep),
    drive: DriveService = Depends(drive_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> FileDetail:
    file, drives = await asyncio.gather(files.get(file_id), drive.list_drives())
    drive_name = next((d.name for d in drives if d.id == file.drive_id), None)
    cached = await cache.peek_drive_tree(db, file.drive_id) if file.drive_id else None
    tree = cached.tree if cached else None
    breadcrumb = build_breadcrumb(tree, file.drive_id, drive_name, file.parent_folder_id)
    return FileDetail(
        **file.model_dump(),
        drive_name=drive_name,
        breadcrumb=breadcrumb,
    )


@router.get("/{file_id}/html")
async def read_file_html(
    file_id: str,
    files: FilesService = Depends(files_service_dep),
) -> DocHtml:
    html, tabs = await asyncio.gather(files.export_html(file_id), files.list_tabs(file_id))
    return DocHtml(html=html, tabs=tabs)


@router.get("/{file_id}/comments")
async def read_file_comments(
    file_id: str,
    files: FilesService = Depends(files_service_dep),
) -> list[DocComment]:
    return await files.list_comments(file_id)


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
    extras: ExtrasService = Depends(extras_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
) -> FileResponse:
    result = await files.move(file_id, payload.new_parent_folder_id)
    await _invalidate_drive_of(db, cache, result.source)
    await _invalidate_drive_of(db, cache, result.moved)
    if result.source.parent_folder_id != result.moved.parent_folder_id:
        await extras.clear_sort_order(file_id)
    return result.moved


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
    result = await files.delete(file_id)
    await _invalidate_drive_of(db, cache, result.source)


async def _invalidate_drive_of(
    db: AsyncSession,
    cache: SidebarCacheService,
    file: FileResponse,
) -> None:
    if file.drive_id:
        await cache.invalidate_drive(db, file.drive_id)
