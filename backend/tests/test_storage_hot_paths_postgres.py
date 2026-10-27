from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, override
import uuid

from cryptography.fernet import Fernet
import httpx
from pydantic import JsonValue
import pytest
from sqlalchemy import Text, column, delete, event, func, inspect, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.auth.encryption import decrypt_cache, encrypt_text
from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleRefreshResult,
    GoogleTokenManager,
    ManagedGoogleToken,
)
from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from app.config import settings
from app.document_transform.schemas import DocumentTransformRequest, DriveFileSource, TransformMode
from app.drive.schemas import DriveSummary, DriveTree
from app.drive.service import GoogleDriveService
from app.extras.schemas import SortOrderWrite
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, FileId, GoogleUserId, IdempotencyKey, UserId
from app.models.task import Task
from app.models.task_replay_key import TaskReplayKey
from app.models.user import User
from app.models.wiki import CacheEntry, DriveTreeCache, UserSortOrder
from app.sidebar.cache import PostgresSidebarCacheService
from app.sidebar.singleflight import Singleflight
from app.tasks.errors import TaskNotFoundError
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
    writes: str,
    interruption: str,
) -> None:
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
                PostgresTaskService(db=db)._lock_document_transform_owner(
                    database.owner, timeout_seconds=0.05
                )
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


async def _seed_google_credentials(database: _Database) -> None:
    async with database.sessions() as db:
        await db.execute(
            update(User)
            .where(User.id == database.owner)
            .values(
                encrypted_access_token=encrypt_text("storage-audit-initial-token"),
                encrypted_refresh_token=encrypt_text("storage-audit-refresh-token"),
                google_access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                google_token_generation=0,
            )
        )
        await db.commit()


def _observed_token_manager(
    db: AsyncSession, http: httpx.AsyncClient, finished: asyncio.Event
) -> GoogleTokenManager:
    class ObservedTokenManager(GoogleTokenManager):
        @override
        async def _refresh_for_provider(
            self,
            user: User,
            *,
            user_id: UserId,
            force_generation: int,
            owner_task: asyncio.Task[object] | None,
        ) -> ManagedGoogleToken:
            try:
                return await super()._refresh_for_provider(
                    user, user_id=user_id, force_generation=force_generation, owner_task=owner_task
                )
            finally:
                finished.set()

    return ObservedTokenManager(db, http)


@pytest.mark.parametrize("bind_to_connection", [False, True])
async def test_provider_refresh_keeps_owner_lock_and_pending_writes_without_stale_token_flush(
    database: _Database, bind_to_connection: bool
) -> None:
    await _seed_google_credentials(database)
    refreshes = 0

    async def oauth_response(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        assert request.url.host == "oauth2.googleapis.com"
        refreshes += 1
        return httpx.Response(
            200,
            json={"access_token": "storage-audit-refreshed-token", "expires_in": 3600},
        )

    async with (
        AsyncExitStack() as stack,
        httpx.AsyncClient(transport=httpx.MockTransport(oauth_response)) as http,
    ):
        bind = (
            await stack.enter_async_context(database.engine.connect())
            if bind_to_connection
            else database.engine
        )
        db = await stack.enter_async_context(AsyncSession(bind=bind, expire_on_commit=False))
        user = await db.get(User, database.owner)
        assert user is not None
        prepared = await GoogleTokenManager(db, http).prepare(user)
        await db.commit()
        await PostgresTaskService(db=db)._lock_document_transform_owner(database.owner)
        user.display_name = "Caller-owned change"

        queries = _Queries()
        event.listen(database.engine.sync_engine, "before_cursor_execute", queries.record)
        try:
            refreshed = await asyncio.to_thread(prepared.refresh_handler, object())
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", queries.record)
        assert refreshed.token == "storage-audit-refreshed-token"
        assert refreshes == 1
        snapshots = [sql for sql in queries.sql if sql.startswith("SELECT")]
        assert len(snapshots) == 1
        assert "users.email" not in snapshots[0]
        assert "users.google_refresh_lease_until" in snapshots[0]
        state = inspect(user)
        assert state is not None
        assert state.attrs.display_name.history.has_changes()
        assert not state.attrs.encrypted_access_token.history.has_changes()
        assert not state.attrs.google_token_generation.history.has_changes()
        assert user.google_token_generation == 1
        async with database.sessions() as observer:
            assert (
                await observer.scalar(
                    select(func.pg_try_advisory_xact_lock(func.hashtextextended(str(user.id), 0)))
                )
            ) is False
            assert (
                await observer.scalar(select(User.display_name).where(User.id == database.owner))
            ) is None
            # A subsequent refresh generation must survive the caller commit.
            await observer.execute(
                update(User)
                .where(User.id == database.owner)
                .values(
                    encrypted_access_token=encrypt_text("storage-audit-next-generation"),
                    google_token_generation=2,
                )
            )
            await observer.commit()
        await db.commit()
        async with database.sessions() as observer:
            row = (
                await observer.execute(
                    select(User.display_name, User.google_token_generation).where(
                        User.id == database.owner
                    )
                )
            ).one()
            assert row == ("Caller-owned change", 2)


async def test_cancelled_provider_owner_clears_independent_lease_and_rolls_back_caller(
    database: _Database,
) -> None:
    await _seed_google_credentials(database)
    provider_started = asyncio.Event()
    refresh_finished = asyncio.Event()
    bridges: list[asyncio.Task[GoogleRefreshResult]] = []

    async def oauth_response(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "oauth2.googleapis.com"
        provider_started.set()
        await asyncio.Event().wait()
        raise AssertionError

    async with httpx.AsyncClient(transport=httpx.MockTransport(oauth_response)) as http:

        async def request() -> None:
            async with database.sessions() as db:
                user = await db.get(User, database.owner)
                assert user is not None
                prepared = await _observed_token_manager(db, http, refresh_finished).prepare(user)
                await db.commit()
                await PostgresTaskService(db=db)._lock_document_transform_owner(database.owner)
                user.display_name = "Must remain uncommitted"
                bridge = asyncio.create_task(asyncio.to_thread(prepared.refresh_handler, object()))
                bridges.append(bridge)
                try:
                    await asyncio.shield(bridge)
                finally:
                    await db.rollback()

        owner = asyncio.create_task(request())
        await asyncio.wait_for(provider_started.wait(), timeout=2)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        await asyncio.wait_for(refresh_finished.wait(), timeout=2)
        assert len(bridges) == 1
        with pytest.raises(asyncio.CancelledError):
            await bridges[0]
    async with database.sessions() as db:
        row = (
            await db.execute(
                select(
                    User.display_name,
                    User.google_refresh_lease_owner,
                    User.google_refresh_lease_until,
                    User.google_token_generation,
                ).where(User.id == database.owner)
            )
        ).one()
        assert row == (None, None, None, 0)
        assert (
            await db.scalar(
                select(
                    func.pg_try_advisory_xact_lock(func.hashtextextended(str(database.owner), 0))
                )
            )
        ) is True


@pytest.mark.parametrize("request_exit", ["completed", "cancelled"])
async def test_shared_drive_flight_refresh_survives_original_request_exit(
    database: _Database, request_exit: str
) -> None:
    await _seed_google_credentials(database)
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()
    exit_request = asyncio.Event()
    follower_started = asyncio.Event()
    leaders: list[asyncio.Task[list[DriveSummary]]] = []
    refreshes = 0
    expected = [DriveSummary(id=DriveId("scope-fixture-drive"), name="Scope fixture")]

    async def oauth_response(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        assert request.url.host == "oauth2.googleapis.com"
        refreshes += 1
        provider_started.set()
        await release_provider.wait()
        return httpx.Response(200, json={"access_token": "scope-fixture-token", "expires_in": 3600})

    class RefreshingDrive(GoogleDriveService):
        @override
        def _list_drives_sync(self) -> list[DriveSummary]:
            handler = self.google_refresh_handler
            assert handler is not None
            handler(object())
            return expected

    async with httpx.AsyncClient(transport=httpx.MockTransport(oauth_response)) as http:

        async def first_request() -> None:
            async with database.sessions() as db:
                user = await db.get(User, database.owner)
                assert user is not None
                prepared = await GoogleTokenManager(db, http).prepare(user)
                await db.commit()
                drive = RefreshingDrive(user, None, refresh_handler=prepared.refresh_handler)
                leaders.append(asyncio.create_task(drive.list_drives()))
                await exit_request.wait()

        async def following_request() -> list[DriveSummary]:
            async with database.sessions() as db:
                user = await db.get(User, database.owner)
                assert user is not None
                prepared = await GoogleTokenManager(db, http).prepare(user)
                await db.commit()
                drive = RefreshingDrive(user, None, refresh_handler=prepared.refresh_handler)
                follower_started.set()
                return await drive.list_drives()

        original = asyncio.create_task(first_request())
        await asyncio.wait_for(provider_started.wait(), timeout=2)
        surviving = asyncio.create_task(following_request())
        await asyncio.wait_for(follower_started.wait(), timeout=2)
        await asyncio.sleep(0)
        if request_exit == "cancelled":
            original.cancel()
            with pytest.raises(asyncio.CancelledError):
                await original
            leaders[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await leaders[0]
        else:
            exit_request.set()
            await original
        release_provider.set()
        assert await asyncio.wait_for(surviving, timeout=2) == expected
        if request_exit == "completed":
            assert await leaders[0] == expected
        assert refreshes == 1
    async with database.sessions() as db:
        assert (
            await db.scalar(select(User.google_token_generation).where(User.id == database.owner))
        ) == 1


@pytest.mark.parametrize("cancel_background", [False, True])
async def test_stale_sidebar_refresh_owns_provider_lifetime_after_request_exit(
    database: _Database, cancel_background: bool
) -> None:
    await _seed_google_credentials(database)
    begin_walk = asyncio.Event()
    provider_started = asyncio.Event()
    release_provider = asyncio.Event()
    refresh_finished = asyncio.Event()
    background_tasks: set[asyncio.Task[None]] = set()
    drive_id = DriveId("scope-fixture-sidebar")
    stale = DriveTree(drive_id=drive_id, folders=[], files=[], files_complete=False)
    updated = stale.model_copy(update={"files_complete": True})

    async def oauth_response(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "oauth2.googleapis.com"
        provider_started.set()
        await release_provider.wait()
        return httpx.Response(200, json={"access_token": "scope-fixture-token", "expires_in": 3600})

    class RefreshingTreeDrive(GoogleDriveService):
        @override
        async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
            assert drive_id == updated.drive_id
            await begin_walk.wait()
            handler = self.google_refresh_handler
            assert handler is not None
            await asyncio.to_thread(handler, object())
            return updated

    async with httpx.AsyncClient(transport=httpx.MockTransport(oauth_response)) as http:

        async def request() -> None:
            async with database.sessions() as db:
                user = await db.get(User, database.owner)
                assert user is not None
                prepared = await _observed_token_manager(db, http, refresh_finished).prepare(user)
                await db.commit()
                cache = PostgresSidebarCacheService(
                    drive=RefreshingTreeDrive(user, None, refresh_handler=prepared.refresh_handler),
                    user=user,
                    singleflight=Singleflight(),
                    bg_tasks=background_tasks,
                    sessionmaker=database.sessions,
                )
                await cache._save_tree(db, stale)
                await db.execute(
                    update(DriveTreeCache)
                    .where(
                        DriveTreeCache.user_id == database.owner,
                        DriveTreeCache.drive_id == drive_id,
                    )
                    .values(
                        refreshed_at=datetime.now(UTC)
                        - settings.SIDEBAR_CACHE_TTL
                        - timedelta(seconds=1)
                    )
                )
                await db.commit()
                cached = await cache.peek_drive_tree(db, drive_id)
                assert cached is not None
                assert cached.tree == stale

        original = asyncio.create_task(request())
        await original
        assert len(background_tasks) == 1
        background = next(iter(background_tasks))
        begin_walk.set()
        await asyncio.wait_for(provider_started.wait(), timeout=2)
        if cancel_background:
            background.cancel()
            with pytest.raises(asyncio.CancelledError):
                await background
        else:
            release_provider.set()
            await asyncio.wait_for(background, timeout=2)
        await asyncio.wait_for(refresh_finished.wait(), timeout=2)
    async with database.sessions() as db:
        row = (
            await db.execute(
                select(
                    User.google_token_generation,
                    User.google_refresh_lease_owner,
                    User.google_refresh_lease_until,
                ).where(User.id == database.owner)
            )
        ).one()
        assert row == (0 if cancel_background else 1, None, None)
        tree_row = await db.scalar(
            select(DriveTreeCache).where(
                DriveTreeCache.user_id == database.owner, DriveTreeCache.drive_id == drive_id
            )
        )
        assert tree_row is not None
        plaintext = decrypt_cache(tree_row.tree)
        assert isinstance(plaintext, str)
        assert DriveTree.model_validate_json(plaintext) == (stale if cancel_background else updated)


@pytest.mark.parametrize("credential_state", ["deleted", "revoked", "rotated"])
async def test_identity_only_provider_refresh_still_reads_fresh_credential_state(
    database: _Database, credential_state: str
) -> None:
    await _seed_google_credentials(database)

    def unexpected_oauth(_request: httpx.Request) -> httpx.Response:
        raise AssertionError

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(unexpected_oauth)) as http,
        database.sessions() as db,
    ):
        user = await db.get(User, database.owner)
        assert user is not None
        prepared = await GoogleTokenManager(db, http).prepare(user)
        await db.commit()
        user.display_name = "Caller-owned pending change"
        async with database.sessions() as observer:
            if credential_state == "deleted":
                await observer.execute(delete(User).where(User.id == database.owner))
            else:
                await observer.execute(
                    update(User)
                    .where(User.id == database.owner)
                    .values(
                        encrypted_access_token=(
                            encrypt_text("round3-other-session-token")
                            if credential_state == "rotated"
                            else None
                        ),
                        encrypted_refresh_token=None,
                        google_access_token_expires_at=(
                            datetime.now(UTC) + timedelta(hours=1)
                            if credential_state == "rotated"
                            else None
                        ),
                        google_token_generation=1,
                        token_version=1,
                    )
                )
            await observer.commit()
        queries = _Queries()
        event.listen(database.engine.sync_engine, "before_cursor_execute", queries.record)
        try:
            if credential_state == "rotated":
                refreshed = await asyncio.to_thread(prepared.refresh_handler, object())
                assert refreshed.token == "round3-other-session-token"
                assert user.google_token_generation == 1
            else:
                with pytest.raises(GoogleReauthenticationRequiredError):
                    await asyncio.to_thread(prepared.refresh_handler, object())
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", queries.record)
        assert len(queries.sql) == 1
        assert queries.sql[0].startswith("SELECT")
        assert user.display_name == "Caller-owned pending change"
        assert inspect(user).attrs.display_name.history.has_changes()


@pytest.mark.parametrize("live_lease", [False, True])
async def test_live_lease_snapshot_skips_doomed_update_but_expired_lease_attempts_cas(
    database: _Database, live_lease: bool
) -> None:
    await _seed_google_credentials(database)
    lease_until = datetime.now(UTC) + timedelta(seconds=10 if live_lease else -10)
    async with database.sessions() as db:
        await db.execute(
            update(User)
            .where(User.id == database.owner)
            .values(google_refresh_lease_owner=uuid.uuid7(), google_refresh_lease_until=lease_until)
        )
        await db.commit()
    async with httpx.AsyncClient() as http, database.sessions() as db:
        manager = GoogleTokenManager(db, http)
        identity = User(id=database.owner)
        snapshot = await manager._snapshot(identity)
        queries = _Queries()
        event.listen(database.engine.sync_engine, "before_cursor_execute", queries.record)
        try:
            acquired = await manager._acquire_lease(
                identity, snapshot, uuid.uuid7(), datetime.now(UTC)
            )
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", queries.record)
        assert acquired is not live_lease
        assert len(queries.sql) == (0 if live_lease else 1)
        assert not db.in_transaction()


async def test_live_lease_waiters_recheck_fresh_generation_without_doomed_updates(
    database: _Database,
) -> None:
    await _seed_google_credentials(database)
    async with database.sessions() as db:
        await db.execute(
            update(User)
            .where(User.id == database.owner)
            .values(
                google_refresh_lease_owner=uuid.uuid7(),
                google_refresh_lease_until=datetime.now(UTC) + timedelta(seconds=10),
            )
        )
        await db.commit()
    observed = asyncio.Event()
    queries = _Queries()

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        queries.record(_connection, _cursor, statement, parameters, _context, _executemany)
        if sum(sql.startswith("SELECT") for sql in queries.sql) >= 2:
            observed.set()

    def unexpected_oauth(_request: httpx.Request) -> httpx.Response:
        raise AssertionError

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_oauth)) as http:

        async def wait_for_token() -> ManagedGoogleToken:
            async with database.sessions() as db:
                return await GoogleTokenManager(db, http).ensure_valid(
                    User(id=database.owner), force_generation=0
                )

        event.listen(database.engine.sync_engine, "before_cursor_execute", record)
        waiting = [asyncio.create_task(wait_for_token()) for _ in range(2)]
        try:
            await asyncio.wait_for(observed.wait(), timeout=2)
            assert not any(sql.startswith("UPDATE") for sql in queries.sql)
            async with database.sessions() as observer:
                await observer.execute(
                    update(User)
                    .where(User.id == database.owner)
                    .values(
                        encrypted_access_token=encrypt_text("round3-winning-generation"),
                        google_token_generation=1,
                        google_refresh_lease_owner=None,
                        google_refresh_lease_until=None,
                    )
                )
                await observer.commit()
            tokens = await asyncio.wait_for(asyncio.gather(*waiting), timeout=2)
            assert [token.token for token in tokens] == ["round3-winning-generation"] * 2
            assert [token.generation for token in tokens] == [1, 1]
            assert sum(sql.startswith("UPDATE") for sql in queries.sql) == 1
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", record)
            for task in waiting:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waiting, return_exceptions=True)


@pytest.mark.parametrize("key_kind", ["original", "alias", "both"])
@pytest.mark.parametrize("visibility", ["retained", "expired", "wrong-owner", "wrong-kind"])
async def test_indexed_key_lookup_preserves_identity_owner_kind_and_expiry(
    database: _Database, key_kind: str, visibility: str
) -> None:
    original, alias = IdempotencyKey(uuid.uuid7()), IdempotencyKey(uuid.uuid7())
    now = datetime.now(UTC)
    task = Task(
        owner_user_id=database.owner,
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        idempotency_key=original,
        request_hash="0" * 64,
        payload={},
        created_at=now - timedelta(days=2),
        expires_at=now + timedelta(days=1),
    )
    async with database.sessions() as db:
        db.add(task)
        await db.flush()
        if key_kind in {"alias", "both"}:
            db.add(
                TaskReplayKey(
                    owner_user_id=database.owner,
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    idempotency_key=alias if key_kind == "alias" else original,
                    task_id=task.id,
                )
            )
        if visibility == "expired":
            task.expires_at = now - timedelta(days=1)
        elif visibility == "wrong-kind":
            task.kind = TaskKind.FILE_SMART_CREATE
        await db.commit()
        task_id = task.id
    key = alias if key_kind == "alias" else original
    owner = database.other_owner if visibility == "wrong-owner" else database.owner
    async with database.sessions() as db:
        service = PostgresTaskService(db=db)
        existing = await service._maybe_existing(owner, TaskKind.FILE_DOCUMENT_TRANSFORM, key)
        if visibility == "retained":
            assert existing is not None
            assert existing.id == task_id
            admission = await service.get_document_transform_admission(owner, key)
            assert admission.task_id == task_id
        else:
            assert existing is None
            with pytest.raises(TaskNotFoundError):
                await service.get_document_transform_admission(owner, key)
