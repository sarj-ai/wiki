from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.extras.dependencies import extras_service_dep
from app.extras.service import ExtrasService
from app.files.breadcrumb import build_breadcrumb
from app.files.dependencies import files_service_dep
from app.files.schemas import (
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

_HTML_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'",
        "img-src data:",
        "font-src data:",
        "connect-src 'none'",
        "media-src 'none'",
        "object-src 'none'",
        "frame-src 'none'",
        "worker-src 'none'",
        "manifest-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        f"frame-ancestors {settings.frontend_origin}",
        "sandbox allow-popups allow-popups-to-escape-sandbox allow-scripts",
    )
)


@router.get("/{file_id}/content", response_class=Response)
async def read_html_content(
    file_id: str,
    files: Annotated[FilesService, Depends(files_service_dep)],
) -> Response:
    content = await files.get_html_content(file_id)
    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": _HTML_CSP,
            "Cross-Origin-Resource-Policy": "same-site",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), clipboard-read=(), clipboard-write=()",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_file(
    payload: FileCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileResponse:
    result = await files.create(payload.parent_folder_id, payload.name, payload.mime_type)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.get("/{file_id}")
async def read_file(
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
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


@router.patch("/{file_id}/rename")
async def rename_file(
    file_id: str,
    payload: FileRename,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileResponse:
    result = await files.rename(file_id, payload.name)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.patch("/{file_id}/move")
async def move_file(
    file_id: str,
    payload: FileMove,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    extras: Annotated[ExtrasService, Depends(extras_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
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
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileResponse:
    result = await files.copy(file_id, payload.name)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
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
