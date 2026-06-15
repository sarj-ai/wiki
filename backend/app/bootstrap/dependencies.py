from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.bootstrap.service import AggregatingBootstrapService, BootstrapService
from app.database import get_db
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.extras.dependencies import extras_service_dep
from app.extras.service import ExtrasService
from app.models.user import User
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep


def bootstrap_service_dep(
    drive: DriveService = Depends(drive_service_dep),
    extras: ExtrasService = Depends(extras_service_dep),
    cache: SidebarCacheService = Depends(sidebar_cache_service_dep),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BootstrapService:
    return AggregatingBootstrapService(drive=drive, extras=extras, cache=cache, db=db, user=user)
