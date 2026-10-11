from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import NamedTuple
import uuid

from cryptography.fernet import Fernet
from pydantic import JsonValue
import pytest
from sqlalchemy import Text, column, delete, event, func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from app.document_transform.schemas import DocumentTransformRequest, DriveFileSource, TransformMode
from app.extras.schemas import SortOrderWrite
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, FileId, GoogleUserId, IdempotencyKey, UserId
from app.models.task import Task
from app.models.user import User
from app.models.wiki import CacheEntry, UserSortOrder
from app.tasks import service as task_service_module
from app.tasks.schemas import TaskKind
from app.tasks.service import PostgresTaskService
from tests.test_task_source_dedupe_postgres import _qa_engine


class _Database(NamedTuple):
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    owner: UserId
    other_owner: UserId
    protector: CacheEntryProtector
    cache_keys: set[str]


@pytest.fixture
async def database() -> AsyncGenerator[_Database]:
    validated = _qa_engine()
    engine = create_async_engine(
        validated.url, pool_size=2, max_overflow=0, pool_timeout=0.5, pool_pre_ping=True
    )
    await validated.dispose()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, other_owner = UserId(uuid.uuid7()), UserId(uuid.uuid7())
    fixture = _Database(
        engine,
        sessions,
        owner,
        other_owner,
        CacheEntryProtector((Fernet.generate_key().decode(),)),
        set(),
    )
    try:
        async with sessions() as db:
            for user_id in (owner, other_owner):
                db.add(
                    User(
                        id=user_id,
                        email=f"storage-round2-{user_id}@sarj.ai",
                        google_id=GoogleUserId(f"storage-round2-{user_id}"),
                        token_version=0,
                    )
                )
            await db.commit()
        yield fixture
    finally:
        async with sessions() as db:
            if fixture.cache_keys:
                await db.execute(delete(CacheEntry).where(CacheEntry.key.in_(fixture.cache_keys)))
            await db.execute(delete(User).where(User.id.in_((owner, other_owner))))
            await db.commit()
        await engine.dispose()


def _values(database: _Database, count: int, value: JsonValue = None) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {
        f"round2-{database.owner}-{index}": value if value is not None else {"index": index}
        for index in range(count)
    }
    for key in values:
        database.cache_keys.update(
            database.protector.physical_keys(CacheNamespace.SHORTCUT_PROBE, key)
        )
    return values


class _Queries:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.parameters: list[object] = []
        self.fail_insert: int | None = None

    def record(
        self,
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        self.sql.append(statement)
        self.parameters.append(parameters)
        if "INSERT INTO cache_entries" in statement:
            if self.fail_insert == 1:
                msg = "Deliberate cache batch failure"
                raise RuntimeError(msg)
            if self.fail_insert is not None:
                self.fail_insert -= 1


@pytest.mark.parametrize("count", [1, 8, 1001, 4096])
async def test_cache_bulk_paths_preserve_values_ttl_and_bounded_round_trips(
    database: _Database, count: int
) -> None:
    cache = CachePSqlService(database.sessions, database.protector)
    values = _values(database, count)
    queries = _Queries()
    event.listen(database.engine.sync_engine, "before_cursor_execute", queries.record)
    try:
        await cache.put_many(CacheNamespace.SHORTCUT_PROBE, values, ttl=timedelta(minutes=5))
        writes = [sql for sql in queries.sql if "INSERT INTO cache_entries" in sql]
        assert len(writes) == (count + 999) // 1000
        if count >= 8:
            assert "unnest(" in writes[0]
            assert "to_jsonb(" in writes[0]
        queries.sql.clear()
        assert await cache.get_many(CacheNamespace.SHORTCUT_PROBE, [*values, *values]) == values
        assert len(queries.sql) == 1
        if count >= 4096:
            assert "JOIN unnest(" in queries.sql[0]
        key = next(iter(values))
        async with database.sessions() as db:
            await db.execute(
                update(CacheEntry)
                .where(
                    CacheEntry.key
                    == database.protector.physical_keys(CacheNamespace.SHORTCUT_PROBE, key)[0]
                )
                .values(expires_at=func.now() - timedelta(seconds=1))
            )
            await db.commit()
        assert await cache.get(CacheNamespace.SHORTCUT_PROBE, key) is None
        assert await cache.get(CacheNamespace.DRIVE_LIST, key) is None
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", queries.record)


async def test_cache_later_array_batch_failure_rolls_back_every_write(database: _Database) -> None:
    values = _values(database, 1001)
    queries = _Queries()
    queries.fail_insert = 2
    cache = CachePSqlService(database.sessions, database.protector)
    event.listen(database.engine.sync_engine, "before_cursor_execute", queries.record)
    try:
        with pytest.raises(RuntimeError, match="Deliberate cache batch failure"):
            await cache.put_many(CacheNamespace.SHORTCUT_PROBE, values, ttl=timedelta(minutes=5))
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", queries.record)
    assert await cache.get_many(CacheNamespace.SHORTCUT_PROBE, list(values)) == {}


async def test_bulk_key_rotation_reads_old_entries_and_writes_only_the_active_lookup_key(
    database: _Database,
) -> None:
    old_key = Fernet.generate_key().decode()
    old = CacheEntryProtector((old_key,))
    rotated = CacheEntryProtector(
        (Fernet.generate_key().decode(), Fernet.generate_key().decode(), old_key)
    )
    values: dict[str, JsonValue] = {
        f"rotated-{database.owner}-{index}": {"generation": "old"} for index in range(32)
    }
    for key in values:
        database.cache_keys.update(rotated.physical_keys(CacheNamespace.SHORTCUT_PROBE, key))
    old_cache = CachePSqlService(database.sessions, old)
    cache = CachePSqlService(database.sessions, rotated)
    await old_cache.put_many(CacheNamespace.SHORTCUT_PROBE, values, ttl=timedelta(minutes=5))
    assert await cache.get_many(CacheNamespace.SHORTCUT_PROBE, list(values)) == values
    latest: dict[str, JsonValue] = dict.fromkeys(values, "new")
    await cache.put_many(CacheNamespace.SHORTCUT_PROBE, latest, ttl=timedelta(minutes=5))
    assert await cache.get_many(CacheNamespace.SHORTCUT_PROBE, list(values)) == latest
    async with database.sessions() as db:
        count = await db.scalar(
            select(func.count())
            .select_from(CacheEntry)
            .where(CacheEntry.key.in_(database.cache_keys))
        )
        # One old row and one active row per logical key; the intermediate
        # rotation candidate is never written by a bulk cache upsert.
        assert count == 2 * len(values)
    removed = next(iter(values))
    await cache.delete(CacheNamespace.SHORTCUT_PROBE, removed)
    assert await old_cache.get(CacheNamespace.SHORTCUT_PROBE, removed) is None
    assert await cache.get(CacheNamespace.SHORTCUT_PROBE, removed) is None


async def test_cache_opposite_input_orders_share_a_consistent_lock_order(
    database: _Database,
) -> None:
    cache = CachePSqlService(database.sessions, database.protector)
    forward = _values(database, 1001, "forward")
    backward: dict[str, JsonValue] = dict.fromkeys(reversed(forward), "backward")
    await cache.put_many(CacheNamespace.SHORTCUT_PROBE, forward, ttl=timedelta(minutes=5))
    queries = _Queries()
    event.listen(database.engine.sync_engine, "before_cursor_execute", queries.record)
    try:
        await asyncio.wait_for(
            asyncio.gather(
                cache.put_many(CacheNamespace.SHORTCUT_PROBE, forward, ttl=timedelta(minutes=5)),
                cache.put_many(CacheNamespace.SHORTCUT_PROBE, backward, ttl=timedelta(minutes=5)),
            ),
            timeout=5,
        )
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", queries.record)
    for sql, parameters in zip(queries.sql, queries.parameters, strict=True):
        if "unnest(" in sql:
            assert isinstance(parameters, tuple)
            arrays = [parameter for parameter in parameters if isinstance(parameter, list)]
            assert len(arrays) == 2
            keys = arrays[0]
            strings = [key for key in keys if isinstance(key, str)]
            assert len(strings) == len(keys)
            assert strings == sorted(strings)
    observed = await cache.get_many(CacheNamespace.SHORTCUT_PROBE, list(forward))
    assert set(observed) == set(forward)
    assert set(observed.values()) in ({"forward"}, {"backward"})


async def test_owner_lock_waiters_release_pool_connections_for_fresh_admission_checks(
    database: _Database,
) -> None:
    first_entered = asyncio.Event()
    admissions = 0

    async def admit() -> None:
        nonlocal admissions
        admissions += 1
        first_entered.set()
        await asyncio.sleep(0.05)
        await CachePSqlService(database.sessions, database.protector).get(
            CacheNamespace.TEMPLATE_CATALOG, "permission-check"
        )

    async def reserve(file_id: str) -> None:
        async with database.sessions() as db:
            # A request has already loaded its user through this shared session.
            user = await db.get(User, database.owner)
            assert user is not None
            await PostgresTaskService(db=db).create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=user.id,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=DocumentTransformRequest(
                    source=DriveFileSource(
                        kind="drive_file",
                        file_id=FileId(file_id),
                        expected_modified_time="2026-10-10T00:00:00Z",
                    ),
                    mode=TransformMode.FORMAT,
                ),
                admit=admit,
            )
            assert user.id == database.owner

    first = asyncio.create_task(reserve("round2-first-source"))
    await first_entered.wait()
    second = asyncio.create_task(reserve("round2-second-source"))
    await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
    assert admissions == 2
    async with database.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(Task).where(Task.owner_user_id == database.owner)
            )
            == 2
        )


@pytest.mark.parametrize("writes", ["none", "pending", "flushed", "core"])
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
async def test_owner_lock_interruption_rolls_back_without_committing_caller_writes(
    database: _Database,
    monkeypatch: pytest.MonkeyPatch,
    writes: str,
    interruption: str,
) -> None:
    # Exercise the production deadline lookup with the actual cancellation path
    # without waiting two minutes on each intentionally blocked transaction.
    monkeypatch.setattr(  # sarj-noqa: SARJ445 -- Test production deadline lookup.
        task_service_module, "_OWNER_LOCK_TIMEOUT_SECONDS", 0.05
    )
    async with database.sessions() as holder, database.sessions() as db:
        await holder.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(str(database.owner), 0)))
        )
        user = await db.get(User, database.owner)
        assert user is not None
        if writes in {"pending", "flushed"}:
            user.display_name = "Must remain uncommitted"
            if writes == "flushed":
                await db.flush()
        elif writes == "core":
            await db.execute(
                update(User)
                .where(User.id == database.owner)
                .values(display_name="Must remain uncommitted")
            )
        queries = _Queries()
        event.listen(database.engine.sync_engine, "before_cursor_execute", queries.record)
        try:
            waiting = asyncio.create_task(
                PostgresTaskService(db=db)._lock_document_transform_owner(database.owner)
            )
            if interruption == "cancel":
                await asyncio.sleep(0.02)
                waiting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await waiting
            else:
                with pytest.raises(TimeoutError):
                    await waiting
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", queries.record)
        assert not db.in_transaction()
        if writes != "none":
            assert any("pg_advisory_xact_lock(" in sql for sql in queries.sql)
        await holder.rollback()
    async with database.sessions() as db:
        assert await db.scalar(select(User.display_name).where(User.id == database.owner)) is None
        assert (
            await db.scalar(
                select(func.count()).select_from(Task).where(Task.owner_user_id == database.owner)
            )
            == 0
        )


@pytest.mark.parametrize("count", [1, 8, 32, 1001])
async def test_sort_order_bulk_writes_skip_unchanged_rows_and_keep_user_isolation(
    database: _Database, count: int
) -> None:
    items = [
        SortOrderWrite(drive_id=DriveId(f"drive-{index}"), sort_order=index + 0.125)
        for index in range(count)
    ]
    async with database.sessions() as db:
        owner = await db.get(User, database.owner)
        other = await db.get(User, database.other_owner)
        assert owner is not None
        assert other is not None
        service = PostgresExtrasService(db=db, user=owner)
        await service.upsert_sort_orders(items)
        await PostgresExtrasService(db=db, user=other).upsert_sort_orders(
            [SortOrderWrite(drive_id=items[0].drive_id, sort_order=-7)]
        )
        versions = select(UserSortOrder.drive_id, column("xmin").cast(Text)).where(
            UserSortOrder.user_id == database.owner
        )
        before = dict((await db.execute(versions)).tuples().all())
        await service.upsert_sort_orders(list(reversed(items)))
        assert dict((await db.execute(versions)).tuples().all()) == before
        changed = items[0].model_copy(update={"sort_order": 42.5})
        await service.upsert_sort_orders([changed, *items[1:]])
        after = dict((await db.execute(versions)).tuples().all())
        assert after.pop(changed.drive_id) != before.pop(changed.drive_id)
        assert after == before
        observed = {item.drive_id: item.sort_order for item in await service.list_sort_orders()}
        assert observed == {item.drive_id: item.sort_order for item in [changed, *items[1:]]}
        other_rows = await PostgresExtrasService(db=db, user=other).list_sort_orders()
        assert [(item.drive_id, item.sort_order) for item in other_rows] == [
            (items[0].drive_id, -7)
        ]
