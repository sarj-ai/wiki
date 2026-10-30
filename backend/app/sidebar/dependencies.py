from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.database import async_session
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.models.user import User
from app.sidebar.cache import (
    BG_TASKS,
    SINGLEFLIGHT,
    PostgresSidebarCacheService,
    SidebarCacheService,
)


async def sidebar_cache_service_dep(
    user: Annotated[User, Depends(get_current_user)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> SidebarCacheService:
    return PostgresSidebarCacheService(
        drive=drive,
        user=user,
        singleflight=SINGLEFLIGHT,
        bg_tasks=BG_TASKS,
        sessionmaker=async_session,
    )
