"""HTTP surface for Navigator file CRUD.

Every endpoint here uses the caller's own OAuth token and hits Drive
directly. The sidebar cache is invalidated inline on writes so the
next sidebar load sees the change — see PIVOT_PLAN.md §7.2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.files import service
from app.files.schemas import FileCopy, FileCreate, FileMove, FileRename, FileResponse
from app.models.user import User
from app.sidebar.cache import invalidate_drive

router = APIRouter(prefix="/files", tags=["files"])


async def _invalidate_drive_of(
    db: AsyncSession,
    file: FileResponse,
) -> None:
    """Drop cached tree for the drive that owns ``file``.

    If the Drive response carried a ``driveId`` (every shared-drive
    file does), we invalidate that drive's cache so the new/renamed/
    moved file shows up on the next sidebar load. A missing
    ``driveId`` means the file is in My Drive and the Navigator
    doesn't cache it anyway.
    """
    if file.drive_id:
        await invalidate_drive(db, file.drive_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_file(
    payload: FileCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    result = await service.create_file(
        user,
        payload.parent_folder_id,
        payload.name,
        payload.mime_type,
    )
    await _invalidate_drive_of(db, result)
    return result


@router.get("/{file_id}")
async def read_file(
    file_id: str,
    user: User = Depends(get_current_user),
) -> FileResponse:
    return await service.get_file(user, file_id)


@router.patch("/{file_id}/rename")
async def rename_file(
    file_id: str,
    payload: FileRename,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    result = await service.rename_file(user, file_id, payload.name)
    await _invalidate_drive_of(db, result)
    return result


@router.patch("/{file_id}/move")
async def move_file(
    file_id: str,
    payload: FileMove,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    # Invalidate the source drive BEFORE the move, since after the move
    # we only know the destination drive.
    source = await service.get_file(user, file_id)
    await _invalidate_drive_of(db, source)

    result = await service.move_file(user, file_id, payload.new_parent_folder_id)
    await _invalidate_drive_of(db, result)
    return result


@router.post("/{file_id}/copy", status_code=status.HTTP_201_CREATED)
async def copy_file(
    file_id: str,
    payload: FileCopy,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FileResponse:
    result = await service.copy_file(user, file_id, payload.name)
    await _invalidate_drive_of(db, result)
    return result


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    source = await service.get_file(user, file_id)
    await service.trash_file(user, file_id)
    await _invalidate_drive_of(db, source)
