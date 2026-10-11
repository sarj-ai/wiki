from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.files.service import FilesService, GoogleFilesService
from app.identifiers import FileId
from app.models.user import User


async def files_service_dep(
    user: Annotated[User, Depends(get_current_user)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> FilesService:
    return GoogleFilesService(user=user, drive=drive)


async def require_accessible_doc(
    drive_id: FileId,
    files: Annotated[FilesService, Depends(files_service_dep)],
) -> FileId:
    await files.require_access(drive_id)
    return drive_id
