from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, override
import uuid

from cryptography.fernet import Fernet, MultiFernet
import pytest
from sqlalchemy import BigInteger, delete, func, literal, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool
from sqlalchemy.sql.elements import ColumnElement

from app.auth import encryption
from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from app.drive.schemas import DriveNode, DriveTree
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import (
    CacheEntry,
    DriveTreeCache,
    UserFolderIcon,
    UserPreferences,
    UserSortOrder,
)
import app.sidebar.cache as sidebar_cache
from app.sidebar.cache import PostgresSidebarCacheService
from app.sidebar.singleflight import Singleflight
from tests.support import dependency
from tests.test_task_source_dedupe_postgres import _qa_engine


class _Database(NamedTuple):
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    user: User
    other: User
    drive_id: DriveId
    cache_keys: list[str]


@pytest.fixture
async def database() -> AsyncGenerator[_Database]:
    validated = _qa_engine()
    engine = create_async_engine(validated.url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    await validated.dispose()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    users = [
        User(
            id=UserId(uuid.uuid7()),
            email=f"projection-{uuid.uuid7()}@sarj.ai",
            google_id=GoogleUserId(f"projection-{uuid.uuid7()}"),
            token_version=0,
        )
        for _ in range(2)
    ]
    cache_keys: list[str] = []
    try:
        async with sessions() as db:
            db.add_all(users)
            await db.commit()
        yield _Database(
            engine, sessions, users[0], users[1], DriveId(str(uuid.uuid7())), cache_keys
        )
    finally:
        async with sessions() as db:
            await db.execute(delete(CacheEntry).where(CacheEntry.key.in_(cache_keys)))
            await db.execute(delete(User).where(User.id.in_([user.id for user in users])))
            await db.commit()
        await engine.dispose()


def _tree(database: _Database, name: str) -> DriveTree:
    return DriveTree(
        drive_id=database.drive_id,
        folders=[],
        files=[
            DriveNode(
                id=FileId("document"),
                name=name,
                mime_type="application/vnd.google-apps.document",
                parent_id=FileId(database.drive_id),
                modified_time=None,
            )
        ],
        files_complete=True,
    )


def _sidebar(database: _Database, user: User) -> PostgresSidebarCacheService:
    return PostgresSidebarCacheService(
        drive=dependency(None),
        user=user,
        singleflight=Singleflight(),
        bg_tasks=set(),
        sessionmaker=database.sessions,
    )


async def _store(database: _Database, tree: DriveTree, user: User, refreshed_at: datetime) -> None:
    async with database.sessions() as db:
        db.add(
            DriveTreeCache(
                user_id=user.id,
                drive_id=database.drive_id,
                tree=encryption.encrypt_cache(tree.model_dump_json()),
                refreshed_at=refreshed_at,
                refreshed_by=user.id,
            )
        )
        await db.commit()


async def _peek(database: _Database, service: PostgresSidebarCacheService) -> DriveTree | None:
    async with database.sessions() as db:
        cached = await service.peek_drive_tree(db, database.drive_id)
        assert not db.in_transaction()
        assert isinstance(database.engine.pool, AsyncAdaptedQueuePool)
        assert database.engine.pool.checkedout() == 0
        return cached.tree if cached is not None else None


@pytest.mark.parametrize("change", ["update", "reinsert"])
async def test_warm_sidebar_reads_fresh_tuple_version_even_with_unchanged_timestamp(
    database: _Database, change: str
) -> None:
    timestamp = datetime.now(UTC)
    original = _tree(database, "Original")
    service = _sidebar(database, database.user)
    await _store(database, original, database.user, timestamp)
    first = await _peek(database, service)
    assert first is not None
    assert await _peek(database, service) is first
    decoder = encryption.cache_decoder_identity()
    decoded = sidebar_cache._decoded_trees.latest(database.user.id, database.drive_id, decoder)
    assert decoded is not None
    async with database.sessions() as db:
        snapshot = await service._load_row(db, database.drive_id, decoded[0])
        assert snapshot is not None
        assert snapshot.tree is None
        assert await db.scalar(select(func.pg_current_xact_id_if_assigned())) is None
    replacement = _tree(database, "Replacement")
    async with database.sessions() as db:
        if change == "reinsert":
            await db.execute(
                delete(DriveTreeCache).where(DriveTreeCache.user_id == database.user.id)
            )
            await db.commit()
        else:
            await db.execute(
                update(DriveTreeCache)
                .where(DriveTreeCache.user_id == database.user.id)
                .values(tree=encryption.encrypt_cache(replacement.model_dump_json()))
            )
            await db.commit()
    if change == "reinsert":
        await _store(database, replacement, database.user, timestamp)
    current = await _peek(database, service)
    assert current == replacement
    assert current is not first
    retained = [
        key
        for key in sidebar_cache._decoded_trees._values
        if key[:2] == (database.user.id, database.drive_id)
    ]
    assert len(retained) == 1
    assert retained[0][2] != decoded[0]


class _EpochSidebar(PostgresSidebarCacheService):
    epoch = 0

    @override
    def _transaction_epoch(self) -> ColumnElement[int]:
        return literal(self.epoch, type_=BigInteger)


async def test_warm_sidebar_epoch_and_principal_changes_cannot_reuse_decoded_tree(
    database: _Database,
) -> None:
    timestamp = datetime.now(UTC)
    await _store(database, _tree(database, "Owner"), database.user, timestamp)
    await _store(database, _tree(database, "Other owner"), database.other, timestamp)
    service = _EpochSidebar(
        drive=dependency(None),
        user=database.user,
        singleflight=Singleflight(),
        bg_tasks=set(),
        sessionmaker=database.sessions,
    )
    original = await _peek(database, service)
    service.epoch = 1
    current = await _peek(database, service)
    assert current == original
    assert current is not original
    other = await _peek(database, _sidebar(database, database.other))
    assert other == _tree(database, "Other owner")
    assert other is not current


class _PausedSidebar(PostgresSidebarCacheService):
    def __init__(
        self, database: _Database, *, started: asyncio.Event, release: asyncio.Event
    ) -> None:
        super().__init__(
            drive=dependency(None),
            user=database.user,
            singleflight=Singleflight(),
            bg_tasks=set(),
            sessionmaker=database.sessions,
        )
        self.started = started
        self.release = release
        self.decoder = encryption.cache_decoder_identity()

    @override
    def _cache_decoder(self) -> MultiFernet:
        return self.decoder

    @override
    async def _decode_tree(self, blob: str, decoder: MultiFernet) -> DriveTree:
        self.started.set()
        await self.release.wait()
        return await super()._decode_tree(blob, decoder)


@pytest.mark.parametrize("corrupt", [False, True])
async def test_decode_handoff_retains_key_snapshot_and_never_deletes_a_replacement(
    database: _Database, *, corrupt: bool
) -> None:
    timestamp = datetime.now(UTC)
    original = _tree(database, "Before rotation")
    await _store(database, original, database.user, timestamp)
    if corrupt:
        async with database.sessions() as db:
            await db.execute(
                update(DriveTreeCache)
                .where(DriveTreeCache.user_id == database.user.id)
                .values(tree="not-an-encrypted-tree")
            )
            await db.commit()
    started, release = asyncio.Event(), asyncio.Event()
    service = _PausedSidebar(database, started=started, release=release)
    pending = asyncio.create_task(_peek(database, service))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        service.decoder = MultiFernet([Fernet(Fernet.generate_key())])
        replacement = _tree(database, "After rotation")
        # The same one-connection pool must be available while decode waits.
        async with database.sessions() as db:
            await db.execute(
                update(DriveTreeCache)
                .where(DriveTreeCache.user_id == database.user.id)
                .values(
                    tree=service.decoder.encrypt(replacement.model_dump_json().encode()).decode()
                )
            )
            await db.commit()
    finally:
        release.set()
    observed = await asyncio.wait_for(pending, timeout=1)
    assert observed is None if corrupt else observed == original
    assert await _peek(database, service) == replacement


@pytest.mark.parametrize("rows", ["none", "preferences", "mixed"])
async def test_typed_sidebar_union_preserves_defaults_nullable_icons_and_user_scope(
    database: _Database, rows: str
) -> None:
    async with database.sessions() as db:
        db.add_all(
            [
                UserPreferences(
                    user_id=database.other.id,
                    show_non_doc_files=False,
                    hidden_workspace_ids=["other"],
                ),
                UserFolderIcon(user_id=database.other.id, folder_drive_id="same-folder", icon="🔒"),
                UserSortOrder(user_id=database.other.id, drive_id="same-drive", sort_order=99),
            ]
        )
        if rows != "none":
            db.add(
                UserPreferences(
                    user_id=database.user.id,
                    show_non_doc_files=False,
                    hidden_workspace_ids=["hidden-one", "hidden-two"],
                )
            )
        if rows == "mixed":
            db.add_all(
                [
                    UserFolderIcon(
                        user_id=database.user.id, folder_drive_id="same-folder", icon=None
                    ),
                    UserSortOrder(user_id=database.user.id, drive_id="same-drive", sort_order=-0.5),
                ]
            )
        await db.commit()
        result = await PostgresExtrasService(db=db, user=database.user).load_sidebar()
    assert result.preferences.show_non_doc_files is (rows == "none")
    assert result.preferences.hidden_workspace_ids == (
        [] if rows == "none" else ["hidden-one", "hidden-two"]
    )
    assert len(result.folder_icons) == len(result.sort_orders) == int(rows == "mixed")
    if rows == "mixed":
        assert result.folder_icons[0].icon is None
        assert result.folder_icons[0].folder_drive_id == "same-folder"
        assert result.sort_orders[0].drive_id == "same-drive"
        assert result.sort_orders[0].sort_order == -0.5


async def test_text_cache_projection_keeps_rotation_and_rejects_nonstring_legacy_values(
    database: _Database,
) -> None:
    old, active = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    previous = CacheEntryProtector((old,))
    protector = CacheEntryProtector((active, old))
    values: dict[str, object] = {
        "valid": previous.protect(CacheNamespace.SHORTCUT_PROBE, "valid", {"name": "📚"}),
        "null": None,
        "object": {"value": "f1.invalid"},
        "array": ["f1.invalid"],
        "number": 1,
        "boolean": True,
    }
    async with database.sessions() as db:
        for key, value in values.items():
            physical = previous.physical_keys(CacheNamespace.SHORTCUT_PROBE, key)[0]
            database.cache_keys.append(physical)
            db.add(
                CacheEntry(
                    namespace=CacheNamespace.SHORTCUT_PROBE,
                    key=physical,
                    value=value,
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
        await db.commit()
    cache = CachePSqlService(database.sessions, protector)
    assert await cache.get(CacheNamespace.SHORTCUT_PROBE, "valid") == {"name": "📚"}
    assert await cache.get_many(CacheNamespace.SHORTCUT_PROBE, list(values)) == {
        "valid": {"name": "📚"}
    }
    for key in set(values) - {"valid"}:
        assert await cache.get(CacheNamespace.SHORTCUT_PROBE, key) is None
