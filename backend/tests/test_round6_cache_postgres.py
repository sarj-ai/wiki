from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
import uuid

from cryptography.fernet import Fernet
from pydantic import JsonValue
from pydantic_core import to_json
from pydantic_settings import BaseSettings
import pytest
from sqlalchemy import delete, event, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.encryption import encrypt_cache_bytes
from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from app.drive.schemas import DriveTree
from app.extras.schemas import PreferencesWrite
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import CacheEntry, DriveTreeCache
from app.sidebar.cache import PostgresSidebarCacheService
from app.sidebar.singleflight import Singleflight
from tests.support import dependency


class _QaSettings(BaseSettings):
    wiki_round6_cache_database_url: str | None = None


class _Database(NamedTuple):
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    users: tuple[User, User]
    protector: CacheEntryProtector


@pytest.fixture
async def round6_database() -> AsyncGenerator[_Database]:
    configured = _QaSettings().wiki_round6_cache_database_url
    if configured is None:
        pytest.skip("Requires the owned round6 loopback cache benchmark database")
    url = make_url(configured)
    assert (url.host, url.port, url.database) == ("127.0.0.1", 15488, "wiki_round6_cache_bench")
    engine = create_async_engine(url, pool_size=2, max_overflow=0, pool_timeout=2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    users = tuple(
        User(
            id=UserId(uuid.uuid7()),
            email=f"round6-test-{uuid.uuid7()}@example.com",
            google_id=GoogleUserId(f"round6-test-{uuid.uuid7()}"),
            token_version=0,
        )
        for _ in range(2)
    )
    assert len(users) == 2
    protector = CacheEntryProtector((Fernet.generate_key().decode(),))
    async with sessions() as db:
        db.add_all(users)
        await db.commit()
    try:
        yield _Database(engine, sessions, (users[0], users[1]), protector)
    finally:
        async with sessions() as db:
            # This database belongs to the round6 cache worker. Remove only this
            # fixture's users and the modern ciphertexts protected by its key.
            await db.execute(delete(User).where(User.id.in_([user.id for user in users])))
            for namespace in [CacheNamespace.DRIVE_LIST, CacheNamespace.TEMPLATE_CATALOG]:
                logical = [f"item-{index}" for index in range(8)]
                if namespace == CacheNamespace.DRIVE_LIST:
                    logical.extend(f"atomic-{index}" for index in range(1_008))
                    logical.extend(f"concurrent-{index}" for index in range(1_008))
                candidates = protector.physical_keys_many(namespace, logical, include_rotation=True)
                await db.execute(
                    delete(CacheEntry).where(
                        CacheEntry.namespace == namespace,
                        CacheEntry.key.in_([key for keys in candidates.values() for key in keys]),
                    )
                )
            await db.commit()
        await engine.dispose()


async def test_reused_sql_keeps_namespace_key_ttl_and_current_value_bindings(
    round6_database: _Database,
) -> None:
    database = round6_database
    service = CachePSqlService(database.sessions, database.protector)
    values: dict[str, JsonValue] = {f"item-{index}": {"version": "first"} for index in range(8)}
    await service.put_many(CacheNamespace.DRIVE_LIST, values, ttl=timedelta(seconds=5))
    await service.put_many(
        CacheNamespace.TEMPLATE_CATALOG,
        {key: {"version": "other namespace"} for key in values},
        ttl=timedelta(seconds=17),
    )
    assert await service.get_many(CacheNamespace.DRIVE_LIST, list(values)) == values
    assert await service.get_many(CacheNamespace.DRIVE_LIST, ["unknown"]) == {}
    assert await service.get_many(CacheNamespace.TEMPLATE_CATALOG, list(values)) == {
        key: {"version": "other namespace"} for key in values
    }
    keys = database.protector.physical_keys(CacheNamespace.DRIVE_LIST, "item-0")
    async with database.sessions() as db:
        await db.execute(
            update(CacheEntry)
            .where(CacheEntry.namespace == CacheNamespace.DRIVE_LIST, CacheEntry.key.in_(keys))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    assert await service.get(CacheNamespace.DRIVE_LIST, "item-0") is None
    assert await service.get(CacheNamespace.TEMPLATE_CATALOG, "item-0") == {
        "version": "other namespace"
    }


class _FailSecondWrite:
    def __init__(self) -> None:
        self.writes = 0

    def before_execute(
        self,
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "INSERT INTO cache_entries" in statement:
            self.writes += 1
            if self.writes == 2:
                msg = "injected later cache chunk failure"
                raise RuntimeError(msg)


async def test_parameterized_cache_write_preserves_later_chunk_rollback(
    round6_database: _Database,
) -> None:
    database = round6_database
    service = CachePSqlService(database.sessions, database.protector)
    values: dict[str, JsonValue] = {f"atomic-{index}": index for index in range(1_008)}
    failure = _FailSecondWrite()
    event.listen(database.engine.sync_engine, "before_cursor_execute", failure.before_execute)
    try:
        with pytest.raises(RuntimeError, match="later cache chunk failure"):
            await service.put_many(CacheNamespace.DRIVE_LIST, values, ttl=timedelta(minutes=1))
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", failure.before_execute)
    assert failure.writes == 2
    assert await service.get_many(CacheNamespace.DRIVE_LIST, list(values)) == {}


async def test_shared_array_statement_preserves_concurrent_atomic_writer_ownership(
    round6_database: _Database,
) -> None:
    database = round6_database
    service = CachePSqlService(database.sessions, database.protector)
    keys = [f"concurrent-{index}" for index in range(1_008)]
    first: dict[str, JsonValue] = dict.fromkeys(keys, "first")
    second: dict[str, JsonValue] = dict.fromkeys(reversed(keys), "second")
    await asyncio.wait_for(
        asyncio.gather(
            service.put_many(CacheNamespace.DRIVE_LIST, first, ttl=timedelta(minutes=1)),
            service.put_many(CacheNamespace.DRIVE_LIST, second, ttl=timedelta(minutes=1)),
        ),
        timeout=10,
    )
    observed = await service.get_many(CacheNamespace.DRIVE_LIST, keys)
    assert observed in (first, second)


async def test_sidebar_reuse_is_owner_scoped_and_detects_same_timestamp_row_replacement(
    round6_database: _Database,
) -> None:
    database = round6_database
    owner, other = database.users
    drive_id = DriveId(f"round6-test-{uuid.uuid7()}")
    stamp = datetime.now(UTC)
    tree = DriveTree(drive_id=drive_id, folders=[], files=[], files_complete=False)
    replacement = tree.model_copy(update={"files_complete": True})
    async with database.sessions() as db:
        db.add(
            DriveTreeCache(
                user_id=owner.id,
                drive_id=drive_id,
                tree=encrypt_cache_bytes(to_json(tree)),
                refreshed_at=stamp,
                refreshed_by=owner.id,
            )
        )
        await db.commit()
    services = [
        PostgresSidebarCacheService(
            drive=dependency(object()),
            user=user,
            singleflight=Singleflight(),
            bg_tasks=set(),
            sessionmaker=database.sessions,
        )
        for user in [owner, other]
    ]
    async with database.sessions() as db:
        cold = await services[0].peek_drive_tree(db, drive_id)
        assert cold is not None
        assert cold.tree == tree
        assert await services[1].peek_drive_tree(db, drive_id) is None
        warm = await services[0].peek_drive_tree(db, drive_id)
        assert warm is not None
        assert warm.tree == tree
    async with database.sessions() as db:
        await db.execute(
            update(DriveTreeCache)
            .where(
                DriveTreeCache.user_id == owner.id,
                DriveTreeCache.drive_id == drive_id,
            )
            .values(tree=encrypt_cache_bytes(to_json(replacement)))
        )
        await db.commit()
    async with database.sessions() as db:
        changed = await services[0].peek_drive_tree(db, drive_id)
        assert changed is not None
        assert changed.tree == replacement
        assert changed.refreshed_at == stamp
        assert not db.in_transaction()


async def test_reused_sidebar_union_observes_current_user_and_fresh_preferences(
    round6_database: _Database,
) -> None:
    database = round6_database
    owner, other = database.users
    async with database.sessions() as db:
        service = PostgresExtrasService(db=db, user=owner)
        await service.upsert_preferences(PreferencesWrite(hidden_workspace_ids=[DriveId("hidden")]))
        first = await service.load_sidebar()
        assert first.preferences.hidden_workspace_ids == ["hidden"]
        await db.commit()
        second = await PostgresExtrasService(db=db, user=other).load_sidebar()
        assert second.preferences.hidden_workspace_ids == []
        await db.commit()
        await service.upsert_preferences(PreferencesWrite(show_non_doc_files=False))
        fresh = await service.load_sidebar()
        assert fresh.preferences.show_non_doc_files is False
        assert fresh.preferences.hidden_workspace_ids == ["hidden"]
        await db.commit()
