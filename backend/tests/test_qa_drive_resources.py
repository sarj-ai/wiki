import asyncio
from typing import Never
import uuid

from app.auth.google_tokens import PreparedGoogleCredentials
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.document_transform.qa_resources import prepare_qa_drive
from app.identifiers import UserId
from app.models.user import User


async def test_qa_drive_receives_resolved_cache_and_preserves_prepared_credentials() -> None:
    cache = await cache_service_dep()
    resolved: list[CacheService] = []

    async def cache_factory() -> CacheService:
        await asyncio.sleep(0)
        resolved.append(cache)
        return cache

    def forbidden_refresh() -> Never:
        message = "Preparing QA resources must not call the Google provider"
        raise AssertionError(message)

    prepared = PreparedGoogleCredentials(
        user=User(id=UserId(uuid.uuid7())), refresh_handler=forbidden_refresh
    )

    drive = await prepare_qa_drive(prepared, cache_factory=cache_factory)

    assert resolved == [cache]
    assert drive._cache is cache
    assert drive._user is prepared.user
    assert drive.google_refresh_handler is prepared.refresh_handler
