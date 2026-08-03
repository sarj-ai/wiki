from __future__ import annotations

from app.cache.service import CachePSqlService, CacheService
from app.database import async_session


def cache_service_dep() -> CacheService:
    return CachePSqlService(async_session)
