from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.drive.service import DriveService, GoogleDriveService
from app.identifiers import DriveId
from app.models.user import User


def drive_service_dep(user: Annotated[User, Depends(get_current_user)]) -> DriveService:
    return GoogleDriveService(user)


async def require_member_drive(
    drive_id: DriveId,
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> DriveId:
    drives = await drive.list_drives()
    if not any(d.id == drive_id for d in drives):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drive not found")
    return drive_id
