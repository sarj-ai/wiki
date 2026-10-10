from app.cache.dependencies import cache_entry_protector_dep, cache_service_dep
from app.cache.protection import CacheEntryProtector
from app.cache.service import CachePSqlService


def test_cache_protector_is_a_process_singleton() -> None:
    first = cache_entry_protector_dep()

    assert isinstance(first, CacheEntryProtector)
    assert cache_entry_protector_dep() is first


async def test_cache_dependency_keeps_the_public_service_contract() -> None:
    assert isinstance(await cache_service_dep(), CachePSqlService)
