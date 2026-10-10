from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.auth.dependencies import get_current_user
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.models.user import User
from app.templates.service import DriveTemplateCatalogService, TemplateCatalogService


async def template_catalog_service_dep(
    user: Annotated[User, Depends(get_current_user)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
) -> TemplateCatalogService:
    return DriveTemplateCatalogService(drive=drive, user_id=user.id, cache=cache)
