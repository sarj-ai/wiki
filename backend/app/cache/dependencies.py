from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.service import CachePSqlService, CacheService
from app.database import get_db


def cache_service_dep(db: Annotated[AsyncSession, Depends(get_db)]) -> CacheService:
    return CachePSqlService(db)
