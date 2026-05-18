"""FastAPI dependency wiring for ``DriveService``.

Every endpoint that touches Drive declares ``svc: DriveService = Depends(
drive_service_dep)`` and gets a freshly-constructed, user-scoped
``GoogleDriveService``. Tests can override ``drive_service_dep`` on the
app's ``dependency_overrides`` map to inject a fake.
"""

from __future__ import annotations

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.drive.service import DriveService, GoogleDriveService
from app.models.user import User


def drive_service_dep(user: User = Depends(get_current_user)) -> DriveService:
    return GoogleDriveService(user)
