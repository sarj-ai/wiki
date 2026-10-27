from collections.abc import Awaitable, Callable

from app.auth.google_tokens import PreparedGoogleCredentials
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.drive.service import GoogleDriveService


async def prepare_qa_drive(
    prepared: PreparedGoogleCredentials,
    *,
    cache_factory: Callable[[], Awaitable[CacheService]] = cache_service_dep,
) -> GoogleDriveService:
    cache = await cache_factory()
    return GoogleDriveService(prepared.user, cache, refresh_handler=prepared.refresh_handler)
