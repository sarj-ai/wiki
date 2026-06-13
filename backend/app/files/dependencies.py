from __future__ import annotations

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.files.service import FilesService, GoogleFilesService
from app.models.user import User


def files_service_dep(
    user: User = Depends(get_current_user),
    drive: DriveService = Depends(drive_service_dep),
) -> FilesService:
    return GoogleFilesService(user=user, drive=drive)
