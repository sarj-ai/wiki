"""FastAPI dependency wiring for ``BootstrapService``."""

from __future__ import annotations

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.bootstrap.service import AggregatingBootstrapService, BootstrapService
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.extras.dependencies import extras_service_dep
from app.extras.service import ExtrasService
from app.models.user import User


def bootstrap_service_dep(
    drive: DriveService = Depends(drive_service_dep),
    extras: ExtrasService = Depends(extras_service_dep),
    user: User = Depends(get_current_user),
) -> BootstrapService:
    return AggregatingBootstrapService(drive=drive, extras=extras, user=user)
