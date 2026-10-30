from __future__ import annotations

from functools import cache

from app.cache.protection import CacheEntryProtector
from app.cache.service import CachePSqlService, CacheService
from app.config import settings
from app.database import async_session


@cache
def cache_entry_protector_dep() -> CacheEntryProtector:
    raw_keys = (settings.CACHE_FERNET_KEY or settings.FERNET_KEY).get_secret_value()
    return CacheEntryProtector.from_key_string(raw_keys)


async def cache_service_dep() -> CacheService:
    return CachePSqlService(async_session, cache_entry_protector_dep())
