from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from threading import Event, get_ident
from typing import NamedTuple, override
import uuid

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.sql import Executable

from app.bootstrap.service import AggregatingBootstrapService
from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from app.drive.schemas import DriveSummary
from app.extras.service import PostgresExtrasService, SidebarExtras
from app.identifiers import FileId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import CacheEntry, DocIcon, UserFolderIcon, UserPreferences, UserSortOrder
from tests.support import dependency
from tests.test_task_source_dedupe_postgres import _qa_engine


class _Database(NamedTuple):
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    owner: User
    other: User
    cache_keys: list[str]
    icon_ids: list[FileId]


@pytest.fixture
async def database() -> AsyncGenerator[_Database]:
    validated = _qa_engine()
    engine = create_async_engine(validated.url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    await validated.dispose()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    users = [
        User(
            id=UserId(uuid.uuid7()),
            email=f"round5-storage-{uuid.uuid7()}@sarj.ai",
            google_id=GoogleUserId(str(uuid.uuid7())),
            token_version=0,
        )
        for _ in range(2)
    ]
    cache_keys: list[str] = []
    icon_ids: list[FileId] = []
    try:
        async with sessions() as db:
            db.add_all(users)
            await db.commit()
        yield _Database(engine, sessions, users[0], users[1], cache_keys, icon_ids)
    finally:
        async with sessions() as db:
            await db.execute(delete(CacheEntry).where(CacheEntry.key.in_(cache_keys)))
            await db.execute(delete(DocIcon).where(DocIcon.drive_id.in_(icon_ids)))
            await db.execute(delete(User).where(User.id.in_([user.id for user in users])))
            await db.commit()
        await engine.dispose()


async def test_shared_cache_statement_binds_fresh_keys_namespace_rotation_and_ttl(
    database: _Database,
) -> None:
    active, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    protector = CacheEntryProtector((active, old))
    previous = CacheEntryProtector((old,))
    cache = CachePSqlService(database.sessions, protector)
    other_cache = CachePSqlService(database.sessions, previous)
    logical = str(database.owner.id)
    expiry = datetime.now(UTC) + timedelta(minutes=1)
    async with database.sessions() as db:
        for namespace in (CacheNamespace.DRIVE_LIST, CacheNamespace.TEMPLATE_CATALOG):
            physical = previous.physical_keys(namespace, logical)[0]
            database.cache_keys.append(physical)
            db.add(
                CacheEntry(
                    namespace=namespace,
                    key=physical,
                    value=previous.protect(namespace, logical, {"namespace": namespace}),
                    expires_at=expiry,
                )
            )
        await db.commit()
    results = await asyncio.gather(
        cache.get(CacheNamespace.DRIVE_LIST, logical),
        cache.get(CacheNamespace.TEMPLATE_CATALOG, logical),
        other_cache.get(CacheNamespace.DRIVE_LIST, logical),
        cache.get(CacheNamespace.DRIVE_LIST, str(database.other.id)),
    )
    assert list(results) == [
        {"namespace": CacheNamespace.DRIVE_LIST},
        {"namespace": CacheNamespace.TEMPLATE_CATALOG},
        {"namespace": CacheNamespace.DRIVE_LIST},
        None,
    ]
    async with database.sessions() as db:
        await db.execute(
            update(CacheEntry)
            .where(CacheEntry.key == database.cache_keys[0])
            .values(value=previous.protect(CacheNamespace.DRIVE_LIST, logical, {"changed": True}))
        )
        await db.commit()
    assert await cache.get(CacheNamespace.DRIVE_LIST, logical) == {"changed": True}
    async with database.sessions() as db:
        await db.execute(
            update(CacheEntry)
            .where(CacheEntry.key == database.cache_keys[0])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
    assert await cache.get(CacheNamespace.DRIVE_LIST, logical) is None
    assert await cache.get(CacheNamespace.TEMPLATE_CATALOG, logical) == {
        "namespace": CacheNamespace.TEMPLATE_CATALOG
    }
    assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
    assert database.engine.pool.checkedout() == 0


class _SignalExtras(PostgresExtrasService):
    def __init__(self, *, signal: asyncio.Event, db: AsyncSession, user: User) -> None:
        super().__init__(db=db, user=user)
        self.signal = signal

    @override
    async def load_sidebar(self) -> SidebarExtras:
        result = await super().load_sidebar()
        assert not self._db.new
        assert not self._db.dirty
        assert not self._db.deleted
        self.signal.set()
        return result


class _CacheReadingDrive:
    def __init__(self, database: _Database, signal: asyncio.Event) -> None:
        self.database = database
        self.signal = signal

    async def list_drives(self) -> list[DriveSummary]:
        await self.signal.wait()
        await CachePSqlService(self.database.sessions).get(
            CacheNamespace.DRIVE_LIST, str(self.database.owner.id)
        )
        return []


async def test_deferred_bootstrap_releases_overlay_read_before_independent_drive_cache_checkout(
    database: _Database,
) -> None:
    async with database.sessions() as db:
        for user, hidden in ((database.owner, "owner"), (database.other, "other")):
            db.add_all(
                [
                    UserPreferences(
                        user_id=user.id,
                        show_non_doc_files=False,
                        hidden_workspace_ids=[hidden],
                    ),
                    UserFolderIcon(user_id=user.id, folder_drive_id="same-folder", icon=None),
                    UserSortOrder(user_id=user.id, drive_id="same-drive", sort_order=-0.5),
                ]
            )
        await db.commit()
    async with database.sessions() as db:
        current_user = await db.get(User, database.owner.id)
        assert current_user is not None
        signal = asyncio.Event()
        service = AggregatingBootstrapService(
            drive=dependency(_CacheReadingDrive(database, signal)),
            extras=_SignalExtras(signal=signal, db=db, user=current_user),
            files=dependency(None),
            cache=dependency(None),
            db=db,
            user=current_user,
        )
        result = await asyncio.wait_for(service.load(defer_doc_icons=True), timeout=1)
        assert result.preferences.hidden_workspace_ids == ["owner"]
        assert result.folder_icons[0].icon is None
        assert result.sort_orders[0].sort_order == -0.5
        assert result.me.id == str(database.owner.id)
        assert result.doc_icons == []
        assert not db.in_transaction()
        assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
        assert database.engine.pool.checkedout() == 0


class _ConversionWitness:
    def __init__(self) -> None:
        self.threads: set[int] = set()
        self.started = Event()
        self.release = Event()
        self.release.set()


class _ObservedDocRow:
    def __init__(
        self, row: Row[FileId, str | None, str | None], witness: _ConversionWitness
    ) -> None:
        self.row = row
        self.witness = witness

    def __iter__(self) -> Iterator[str | None]:
        self.witness.threads.add(get_ident())
        self.witness.started.set()
        assert self.witness.release.wait(timeout=2)
        return iter(self.row)


class _ObservedRows:
    def __init__(
        self,
        rows: Sequence[Row[FileId, str | None, str | None]],
        witness: _ConversionWitness,
    ) -> None:
        self.rows = rows
        self.witness = witness

    def all(self) -> list[_ObservedDocRow]:
        return [_ObservedDocRow(row, self.witness) for row in self.rows]


class _ObservedReadSession:
    def __init__(self, db: AsyncSession, witness: _ConversionWitness) -> None:
        self.db = db
        self.witness = witness

    async def execute(self, statement: Executable) -> _ObservedRows:
        return _ObservedRows((await self.db.execute(statement)).all(), self.witness)

    async def commit(self) -> None:
        await self.db.commit()


async def _seed_icons(database: _Database, count: int) -> None:
    database.icon_ids.extend(FileId(f"{database.owner.id}-{index}") for index in range(count))
    async with database.sessions() as db:
        db.add_all(
            DocIcon(drive_id=icon_id, icon="📚" if index % 2 else None, cover_url=None)
            for index, icon_id in enumerate(database.icon_ids)
        )
        await db.commit()


@pytest.mark.parametrize("count", [32, 4096])
async def test_icon_model_conversion_preserves_rows_and_selects_worker_only_for_large_lists(
    database: _Database, count: int
) -> None:
    await _seed_icons(database, count)
    async with database.sessions() as db:
        witness = _ConversionWitness()
        service = PostgresExtrasService(
            db=dependency(_ObservedReadSession(db, witness)), user=database.owner
        )
        icons = await service.list_doc_icons()
        expected = set(database.icon_ids)
        current = [icon for icon in icons if icon.drive_id in expected]
        assert len(current) == count
        assert {icon.drive_id for icon in current} == expected
        assert sum(icon.icon is None for icon in current) == count // 2
        assert witness.threads
        assert (get_ident() in witness.threads) is (count < 4096)
        assert not db.in_transaction()


async def test_large_icon_worker_has_released_its_read_connection_before_conversion(
    database: _Database,
) -> None:
    await _seed_icons(database, 4096)
    async with database.sessions() as db:
        witness = _ConversionWitness()
        service = PostgresExtrasService(
            db=dependency(_ObservedReadSession(db, witness)), user=database.owner
        )
        witness.release.clear()
        pending = asyncio.create_task(service.list_doc_icons())
        try:
            assert await asyncio.wait_for(asyncio.to_thread(witness.started.wait, 1), timeout=1.5)
            assert not db.in_transaction()
            assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
            assert database.engine.pool.checkedout() == 0
            async with database.sessions() as other:
                assert await other.scalar(select(User.id).where(User.id == database.owner.id))
        finally:
            witness.release.set()
        assert len(await asyncio.wait_for(pending, timeout=1)) >= 4096
