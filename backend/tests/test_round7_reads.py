from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from pydantic_core import to_json
import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.aliases.errors import AliasNotFoundError
from app.aliases.service import PostgresAliasService
from app.auth.encryption import encrypt_cache_bytes
from app.bootstrap.service import AggregatingBootstrapService
from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from app.drive.schemas import DriveSummary
from app.drive.service import GoogleDriveService
from app.extras.schemas import DocIconRead
from app.extras.service import list_accessible_doc_icons
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId
from app.models.wiki import CacheEntry, DocumentAlias, DriveTreeCache
from app.sidebar.cache import PostgresSidebarCacheService, tree_etag
from app.sidebar.singleflight import Singleflight
from tests.round7_reads_support import (
    ReadsDatabase,
    ReadsDrive,
    ReadsDriveCache,
    ReadsExtras,
    ReadsFailureError,
    ReadsFiles,
    ReadsGate,
    ReadsMembershipDrive,
    ReadsMetadataClient,
    reads_database,
    reads_file,
    reads_url,
    seed_alias,
    seed_trees,
)
from tests.support import dependency


@pytest.fixture
async def round7_reads_database() -> AsyncGenerator[ReadsDatabase]:
    url = reads_url()
    if url is None:
        pytest.skip("Requires the owned round7 loopback reads database")
    async with reads_database(url) as database:
        yield database


@contextmanager
def _record_tree_selects(db: AsyncSession) -> Generator[list[str]]:
    assert isinstance(db.bind, AsyncEngine)
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("SELECT") and "user_drive_tree_cache" in statement:
            statements.append(statement)

    event.listen(db.bind.sync_engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(db.bind.sync_engine, "before_cursor_execute", record)


def _conditional_tree_cache(database: ReadsDatabase) -> PostgresSidebarCacheService:
    return PostgresSidebarCacheService(
        drive=dependency(ReadsDrive()),
        user=database.user,
        singleflight=Singleflight(),
        bg_tasks=set(),
        sessionmaker=database.sessions,
    )


async def test_conditional_tree_read_reuses_one_fresh_version_and_releases_transaction(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    (drive_id,) = await seed_trees(database, count=1, description_bytes=0)
    service = _conditional_tree_cache(database)
    async with database.sessions() as db:
        with _record_tree_selects(db) as statements:
            changed = await service.get_drive_tree_if_modified(
                db, drive_id, request_etag='W/"different-drive:old"'
            )
        assert len(statements) == 1
        assert changed.cached is not None
        assert not db.in_transaction()
        current_tag = tree_etag(drive_id, changed.refreshed_at)
        assert current_tag is not None
        with _record_tree_selects(db) as statements:
            unchanged = await service.get_drive_tree_if_modified(
                db, drive_id, request_etag=current_tag
            )
        assert len(statements) == 1
        assert unchanged.cached is None
        assert unchanged.refreshed_at == changed.refreshed_at
        assert not db.in_transaction()
        spelling_changed = await service.get_drive_tree_if_modified(
            db, drive_id, request_etag=current_tag.replace("+00:00", "Z")
        )
        assert spelling_changed.cached is not None
        assert spelling_changed.cached.tree is changed.cached.tree


async def test_conditional_tree_read_observes_same_timestamp_ciphertext_replacement(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    (drive_id,) = await seed_trees(database, count=1, description_bytes=0)
    service = _conditional_tree_cache(database)
    async with database.sessions() as db:
        original = await service.get_drive_tree(db, drive_id)
        replacement = original.tree.model_copy(deep=True)
        replacement.files[0].name = "Concurrent fresh name"
        async with database.sessions.begin() as writer:
            await writer.execute(
                update(DriveTreeCache)
                .where(
                    DriveTreeCache.user_id == database.user.id,
                    DriveTreeCache.drive_id == drive_id,
                )
                .values(tree=encrypt_cache_bytes(to_json(replacement)))
            )
        changed = await service.get_drive_tree_if_modified(
            db, drive_id, request_etag='W/"different-drive:old"'
        )
        assert changed.cached is not None
        assert changed.cached.tree.files[0].name == "Concurrent fresh name"
        assert original.tree.files[0].name == "Round7 fixture document"
        assert changed.refreshed_at == original.refreshed_at
        current_tag = tree_etag(drive_id, changed.refreshed_at)
        assert current_tag is not None
        async with database.sessions.begin() as writer:
            await writer.execute(
                update(DriveTreeCache)
                .where(
                    DriveTreeCache.user_id == database.user.id,
                    DriveTreeCache.drive_id == drive_id,
                )
                .values(tree="fixture-corrupt-ciphertext")
            )
        unchanged = await service.get_drive_tree_if_modified(db, drive_id, request_etag=current_tag)
        assert unchanged.cached is None
        assert not db.in_transaction()


async def test_singleton_bulk_cache_preserves_a_present_json_null(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    service = CachePSqlService(database.sessions, database.protector)
    namespace = CacheNamespace.SHORTCUT_PROBE
    database.register_cache(namespace, {"round7-fixture-null": None})
    await service.put(namespace, "round7-fixture-null", None, ttl=timedelta(minutes=1))
    assert await service.get_many(namespace, ["round7-fixture-null"]) == {
        "round7-fixture-null": None
    }
    assert await service.get_many(namespace, ["round7-fixture-absent"]) == {}


@pytest.mark.parametrize("failed_read", ["drive", "extras"])
async def test_bootstrap_drains_the_companion_before_propagating_failure(
    round7_reads_database: ReadsDatabase, failed_read: str
) -> None:
    database = round7_reads_database
    gates = {name: ReadsGate(fail=name == failed_read) for name in ["drive", "extras"]}
    async with database.sessions() as db:
        service = AggregatingBootstrapService(
            drive=dependency(ReadsDrive(gate=gates["drive"])),
            extras=dependency(ReadsExtras(gate=gates["extras"])),
            files=dependency(ReadsFiles()),
            cache=dependency(object()),
            db=db,
            user=database.user,
        )
        request = asyncio.create_task(service.load(defer_doc_icons=True))
        await asyncio.gather(*(gate.started.wait() for gate in gates.values()))
        gates[failed_read].release.set()
        with pytest.raises(ReadsFailureError, match="fixture read failed"):
            await request
        assert not any(gate.active for gate in gates.values())
        companion = gates["extras" if failed_read == "drive" else "drive"]
        assert companion.cancelled
        assert not db.in_transaction()


async def test_bootstrap_request_cancellation_drains_both_reads(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    drive_gate, extras_gate = ReadsGate(), ReadsGate()
    async with database.sessions() as db:
        service = AggregatingBootstrapService(
            drive=dependency(ReadsDrive(gate=drive_gate)),
            extras=dependency(ReadsExtras(gate=extras_gate)),
            files=dependency(ReadsFiles()),
            cache=dependency(object()),
            db=db,
            user=database.user,
        )
        request = asyncio.create_task(service.load(defer_doc_icons=True))
        await asyncio.gather(drive_gate.started.wait(), extras_gate.started.wait())
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert drive_gate.cancelled
        assert extras_gate.cancelled
        assert not drive_gate.active
        assert not extras_gate.active


@pytest.mark.parametrize("cancel_request", [False, True])
async def test_bootstrap_cleanup_preserves_native_shared_drive_discovery(
    round7_reads_database: ReadsDatabase, *, cancel_request: bool
) -> None:
    database = round7_reads_database
    cache_gate, extras_gate = ReadsGate(), ReadsGate(fail=True)
    inventory_cache = ReadsDriveCache(cache_gate)
    drive = GoogleDriveService(database.user, cache_service=inventory_cache)
    async with database.sessions() as db:
        service = AggregatingBootstrapService(
            drive=drive,
            extras=dependency(ReadsExtras(gate=extras_gate)),
            files=dependency(ReadsFiles()),
            cache=dependency(object()),
            db=db,
            user=database.user,
        )
        request = asyncio.create_task(service.load(defer_doc_icons=True))
        await asyncio.gather(cache_gate.started.wait(), extras_gate.started.wait())
        survivor = asyncio.create_task(drive.list_drives())
        await asyncio.sleep(0)
        if cancel_request:
            request.cancel()
        else:
            extras_gate.release.set()
        with pytest.raises(asyncio.CancelledError if cancel_request else ReadsFailureError):
            await request
        assert cache_gate.active
        assert not cache_gate.cancelled
        assert not survivor.done()
        cache_gate.release.set()
        assert [item.id for item in await survivor] == ["round7-fixture-shared-drive"]
        assert inventory_cache.calls == 1


async def test_alias_resolve_reads_retirement_even_with_a_retained_orm_identity(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    file_id = FileId("round7-fixture-fresh-alias")
    alias = await seed_alias(database, file_id)
    files = ReadsFiles([reads_file(file_id)])
    async with database.sessions() as db:
        held_identity = await db.get(DocumentAlias, alias)
        assert held_identity is not None
        await db.commit()
        async with database.sessions() as editor:
            await editor.execute(
                update(DocumentAlias)
                .where(DocumentAlias.alias == alias)
                .values(retired_at=datetime.now(UTC))
            )
            await editor.commit()
        assert held_identity.retired_at is None
        service = PostgresAliasService(db=db, files=dependency(files), user=database.user)
        with pytest.raises(AliasNotFoundError):
            await service.resolve(alias)
        assert files.access_calls == []


async def test_alias_resolve_projects_a_fresh_target_then_rechecks_access(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    file_id = FileId("round7-fixture-access-alias")
    alias = await seed_alias(database, file_id)
    files = ReadsFiles([reads_file(file_id)])
    async with database.sessions() as db:
        service = PostgresAliasService(db=db, files=dependency(files), user=database.user)
        assert (await service.resolve(alias)).file_id == file_id
        assert not db.in_transaction()
        files.files.clear()
        with pytest.raises(AliasNotFoundError):
            await service.resolve(alias)
        assert files.access_calls == [file_id, file_id]
        assert not db.in_transaction()


@pytest.mark.parametrize(
    "drive_id",
    [pytest.param(None, id="no-drive"), pytest.param(DriveId("round7-fixture-drive"), id="drive")],
)
async def test_recent_aliases_keep_inventory_for_accessible_metadata(
    round7_reads_database: ReadsDatabase, drive_id: DriveId | None
) -> None:
    database = round7_reads_database
    file_id = FileId("round7-fixture-recent-alias")
    alias = await seed_alias(database, file_id)
    files = ReadsFiles([reads_file(file_id, drive_id=drive_id)])
    drive = ReadsDrive([DriveSummary(id=DriveId("round7-fixture-drive"), name="Fixture drive")])
    async with database.sessions() as db:
        service = PostgresAliasService(
            db=db, files=dependency(files), drive=dependency(drive), user=database.user
        )
        result = await service.resolve_recent([alias, alias, "absent-alias"])
        assert len(result) == 1
        assert result[0].alias == alias
        assert result[0].drive_name == (None if drive_id is None else "Fixture drive")
        assert drive.calls == 1
        assert files.bulk_calls == [[file_id]]
        assert not db.in_transaction()


@pytest.mark.parametrize("authorized", [False, True])
async def test_native_recent_metadata_normalizes_null_provider_drive_before_inventory(
    round7_reads_database: ReadsDatabase, *, authorized: bool
) -> None:
    database = round7_reads_database
    file_id = FileId("round7-fixture-native-recent")
    alias = await seed_alias(database, file_id)
    drive_id = DriveId("round7-fixture-personal-drive")
    drive = ReadsMembershipDrive(drive_id if authorized else None)
    client = ReadsMetadataClient()
    files = GoogleFilesService(
        user=database.user,
        drive=dependency(drive),
        client_factory=dependency(lambda _user, _refresh: client),
    )
    metadata = await files.get_search_metadata_many([file_id])
    assert [file.drive_id for file in metadata.values()] == ([drive_id] if authorized else [])
    async with database.sessions() as db:
        service = PostgresAliasService(
            db=db, files=files, drive=dependency(drive), user=database.user
        )
        result = await service.resolve_recent([alias])
        assert [item.drive_name for item in result] == (
            ["Fixture personal drive"] if authorized else []
        )
        assert drive.calls == (1 if authorized else 0)


@pytest.mark.parametrize("icon_count", [0, 2])
async def test_document_icon_empty_fast_path_keeps_nonempty_permission_filtering(
    icon_count: int,
) -> None:
    icons = [
        DocIconRead(drive_id=FileId(f"round7-fixture-doc-{index}"), icon="📚", cover_url=None)
        for index in range(icon_count)
    ]
    extras = ReadsExtras(icons)
    files = ReadsFiles([reads_file(FileId("round7-fixture-doc-0"))])
    result = await list_accessible_doc_icons(dependency(extras), dependency(files))
    assert result == icons[:1]
    assert files.icon_calls == ([] if not icons else [[icon.drive_id for icon in icons]])


async def test_decoded_payload_budget_evicts_old_large_trees_and_keeps_recent_identity(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    drives = await seed_trees(database, count=14, description_bytes=2 * 1024 * 1024)
    service = PostgresSidebarCacheService(
        drive=dependency(object()),
        user=database.user,
        singleflight=Singleflight(),
        bg_tasks=set(),
        sessionmaker=database.sessions,
    )
    async with database.sessions() as db:
        first = await service.peek_drive_tree(db, drives[0])
        last = None
        for drive_id in drives[1:]:
            last = await service.peek_drive_tree(db, drive_id)
        assert first is not None
        assert last is not None
        warm = await service.peek_drive_tree(db, drives[-1])
        assert warm is not None
        assert warm.tree is last.tree
        evicted = await service.peek_drive_tree(db, drives[0])
        assert evicted is not None
        assert evicted.tree == first.tree
        assert evicted.tree is not first.tree
        assert not db.in_transaction()


async def test_payload_budget_keeps_dynamic_node_accounting_after_public_tree_growth(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    drives = await seed_trees(database, count=2, description_bytes=0)
    service = PostgresSidebarCacheService(
        drive=dependency(object()),
        user=database.user,
        singleflight=Singleflight(),
        bg_tasks=set(),
        sessionmaker=database.sessions,
    )
    async with database.sessions() as db:
        first = await service.peek_drive_tree(db, drives[0])
        assert first is not None
        first.tree.files.extend([first.tree.files[0]] * 40_000)
        assert await service.peek_drive_tree(db, drives[1]) is not None
        fresh = await service.peek_drive_tree(db, drives[0])
        assert fresh is not None
        assert fresh.tree is not first.tree
        assert len(first.tree.files) == 40_001
        assert len(fresh.tree.files) == 1


async def test_bulk_cache_keeps_valid_old_rotation_after_invalid_new_ciphertext(
    round7_reads_database: ReadsDatabase,
) -> None:
    database = round7_reads_database
    namespace = CacheNamespace.SHORTCUT_PROBE
    logical = "round7-fixture-rotation"
    database.register_cache(namespace, {logical: {"generation": "old"}})
    old = CachePSqlService(database.sessions, CacheEntryProtector((database.rotation_keys[-1],)))
    current = CachePSqlService(database.sessions, database.protector)
    await old.put(namespace, logical, {"generation": "old"}, ttl=timedelta(minutes=1))
    await current.put(namespace, logical, {"generation": "new"}, ttl=timedelta(minutes=1))
    async with database.sessions() as db:
        await db.execute(
            update(CacheEntry)
            .where(
                CacheEntry.namespace == namespace,
                CacheEntry.key == database.protector.physical_keys(namespace, logical)[0],
            )
            .values(value="f1.invalid-round7-fixture")
        )
        await db.commit()
    assert await current.get_many(namespace, [logical]) == {logical: {"generation": "old"}}


class ReadsInterruption(BaseException):
    pass


async def test_lazy_singleflight_preserves_base_exception_for_followers_and_retry() -> None:
    singleflight = Singleflight[str]()
    started, release = asyncio.Event(), asyncio.Event()

    async def interrupted() -> str:
        started.set()
        await release.wait()
        raise ReadsInterruption

    leader = asyncio.create_task(singleflight.do("round7-fixture", interrupted))
    await started.wait()
    follower = asyncio.create_task(singleflight.do("round7-fixture", interrupted))
    await asyncio.sleep(0)
    release.set()
    outcomes = await asyncio.gather(leader, follower, return_exceptions=True)
    assert all(isinstance(outcome, ReadsInterruption) for outcome in outcomes)

    async def retried() -> str:
        return "retry completed"

    assert await singleflight.do("round7-fixture", retried) == "retry completed"
