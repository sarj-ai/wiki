from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
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
from app.identifiers import FileId
from app.openapi import authentication_responses
from app.performance import increment
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep


router = APIRouter(prefix="/files", tags=["files"], responses=authentication_responses())

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


@router.get(
    "/{file_id}/content",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    operation_id="read_file_content",
    responses={
        200: {
            "description": "Rendered document content.",
            "content": {"text/html": {"schema": {"type": "string"}}},
        }
    },
)
async def read_html_content(
    file_id: Annotated[FileId, Path(min_length=1)],
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


@router.post("", status_code=status.HTTP_201_CREATED, operation_id="create_file")
async def create_file(
    payload: Annotated[FileCreate, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileResponse:
    result = await files.create(payload.parent_folder_id, payload.name, payload.mime_type)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.get(
    "/{file_id}",
    status_code=status.HTTP_200_OK,
    operation_id="read_file",
)
async def read_file(
    file_id: Annotated[FileId, Path(min_length=1)],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileDetail:
    file, drives = await asyncio.gather(files.get(file_id), drive.list_drives())
    drive_name = next((d.name for d in drives if d.id == file.drive_id), None)
    cached = await cache.peek_drive_tree(db, file.drive_id) if file.drive_id else None
    increment("file_tree_cache_hit" if cached is not None else "file_tree_cache_miss")
    tree = cached.tree if cached else None
    breadcrumb = build_breadcrumb(tree, file.drive_id, drive_name, file.parent_folder_id)
    return FileDetail(
        **file.model_dump(),
        drive_name=drive_name,
        breadcrumb=breadcrumb,
    )


@router.patch(
    "/{file_id}/rename",
    status_code=status.HTTP_200_OK,
    operation_id="rename_file",
)
async def rename_file(
    file_id: Annotated[FileId, Path(min_length=1)],
    payload: Annotated[FileRename, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileResponse:
    result = await files.rename(file_id, payload.name)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.patch(
    "/{file_id}/move",
    status_code=status.HTTP_200_OK,
    operation_id="move_file",
)
async def move_file(
    file_id: Annotated[FileId, Path(min_length=1)],
    payload: Annotated[FileMove, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileResponse:
    result = await files.move(file_id, payload.new_parent_folder_id)
    await _invalidate_drive_of(db, cache, result.source)
    if result.moved.drive_id != result.source.drive_id:
        await _invalidate_drive_of(db, cache, result.moved)
    return result.moved


@router.post(
    "/{file_id}/copy",
    status_code=status.HTTP_201_CREATED,
    operation_id="copy_file",
)
async def copy_file(
    file_id: Annotated[FileId, Path(min_length=1)],
    payload: Annotated[FileCopy, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> FileResponse:
    result = await files.copy(file_id, payload.name)
    await _invalidate_drive_of(db, cache, result)
    return result


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_file",
)
async def delete_file(
    file_id: Annotated[FileId, Path(min_length=1)],
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
