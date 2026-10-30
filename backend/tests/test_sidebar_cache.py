from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple, override
import uuid

from cryptography.fernet import MultiFernet
import pytest
from sqlalchemy.sql import ClauseElement

from app.auth.encryption import cache_decoder_identity, decrypt_cache, encrypt_cache
from app.config import settings
from app.drive.schemas import DriveNode, DriveTree, SearchResult, SnippetResult
from app.drive.service import DriveService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import DriveTreeCache
import app.sidebar.cache as sidebar_cache
from app.sidebar.cache import CACHE_MAX_AGE, CachedDriveTree, PostgresSidebarCacheService
from app.sidebar.singleflight import Singleflight
from tests.support import dependency


def _user() -> User:
    u = User()
    u.id = UserId(uuid.uuid7())
    u.email = "test@sarj.ai"
    u.google_id = GoogleUserId("gid")
    u.token_version = 0
    return u


def _tree() -> DriveTree:
    return DriveTree(
        drive_id=DriveId("drive-1"),
        folders=[
            DriveNode(
                id=FileId("folder-1"),
                name="Quarterly Plans",
                mime_type="application/vnd.google-apps.folder",
                parent_id=FileId("drive-1"),
                modified_time=None,
            )
        ],
        files=[],
        files_complete=True,
    )


class FakeDrive(DriveService):  # sarj-noqa: SARJ428 -- Used only by this module's cache tests.
    def __init__(self, tree: DriveTree) -> None:
        self.tree = tree
        self.walks = 0

    @override
    async def list_drives(self) -> list[Any]:
        return []

    @override
    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        self.walks += 1
        return self.tree

    @override
    async def list_folder_children(self, folder_id: str) -> list[DriveNode]:
        return []

    @override
    async def search_drive(
        self, drive_id: str, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        return []

    @override
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        return []

    @override
    async def fetch_snippet(
        self, file_id: str, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        raise NotImplementedError


class BlockingDrive(FakeDrive):
    def __init__(self, tree: DriveTree, started: asyncio.Event, release: asyncio.Event) -> None:
        super().__init__(tree)
        self.started = started
        self.release = release

    @override
    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        self.walks += 1
        self.started.set()
        await self.release.wait()
        return self.tree


class _SnapshotResult(NamedTuple):
    refreshed_at: datetime
    transaction_epoch: int
    transaction_id: str
    tuple_location: str
    tree: str


class _ScalarResult:
    def __init__(self, value: datetime | None, *, row: DriveTreeCache | None = None) -> None:
        self.value = value
        self.row = row

    def scalar_one_or_none(self) -> datetime | None:
        return self.value

    def one_or_none(self) -> _SnapshotResult | None:
        if self.row is None:
            return None
        return _SnapshotResult(self.row.refreshed_at, 0, "1", "(0,1)", self.row.tree)


class FakeSession:  # sarj-noqa: SARJ428 -- Cache transaction recorder used only in this module.
    def __init__(self, row: DriveTreeCache | None = None) -> None:
        self.row = row
        self.executed: list[ClauseElement] = []
        self.commits = 0

    async def execute(
        self, stmt: ClauseElement, parameters: dict[str, object] | None = None
    ) -> _ScalarResult:
        self.executed.append(stmt.params(parameters) if parameters is not None else stmt)
        return _ScalarResult(self.row.refreshed_at if self.row is not None else None, row=self.row)

    async def commit(self) -> None:
        self.commits += 1


def _db(session: FakeSession) -> Any:
    return session


def _service(
    drive: FakeDrive,
    save_session: FakeSession,
    *,
    singleflight: Singleflight[sidebar_cache.SidebarFlightResult] | None = None,
    user: User | None = None,
) -> PostgresSidebarCacheService:
    @asynccontextmanager
    async def fake_sessionmaker() -> AsyncGenerator[FakeSession]:
        yield save_session

    return PostgresSidebarCacheService(
        drive=drive,
        user=user or _user(),
        singleflight=singleflight or Singleflight(),
        bg_tasks=set(),
        sessionmaker=dependency(fake_sessionmaker),
    )


def test_warm_row_round_trips_through_encryption() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id=DriveId("drive-1"),
        tree=encrypt_cache(tree.model_dump_json()),
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    service = _service(FakeDrive(tree), FakeSession())

    cached = asyncio.run(service.get_drive_tree(_db(db), DriveId("drive-1")))

    assert cached.tree == tree
    assert cached.refreshed_at == row.refreshed_at
    assert len(db.executed) == 1


def test_tree_lookup_uses_principal_and_drive_composite_key() -> None:
    tree = _tree()
    user = _user()
    row = DriveTreeCache(
        user_id=user.id,
        drive_id=DriveId("drive-1"),
        tree=encrypt_cache(tree.model_dump_json()),
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    service = _service(FakeDrive(tree), FakeSession(), user=user)

    asyncio.run(service.peek_drive_tree(_db(db), DriveId("drive-1")))

    compiled = db.executed[0].compile()
    params = compiled.params
    assert params is not None
    assert params["tree_user_id"] == user.id
    assert params["tree_drive_id"] == "drive-1"
    assert "user_drive_tree_cache.xmin" in str(compiled)
    assert "user_drive_tree_cache.ctid" in str(compiled)


def test_metadata_lookup_selects_no_encrypted_tree_blob() -> None:
    tree = _tree()
    user = _user()
    row = DriveTreeCache(
        user_id=user.id,
        drive_id=DriveId("drive-1"),
        tree=encrypt_cache(tree.model_dump_json()),
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    service = _service(FakeDrive(tree), FakeSession(), user=user)

    refreshed_at = asyncio.run(service.get_drive_tree_refreshed_at(_db(db), DriveId("drive-1")))

    statement = str(db.executed[0].compile())
    assert refreshed_at == row.refreshed_at
    assert "SELECT user_drive_tree_cache.refreshed_at" in statement
    assert "user_drive_tree_cache.tree" not in statement
    assert "user_drive_tree_cache.user_id" in statement


def test_undecryptable_row_is_treated_as_cache_miss() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id=DriveId("drive-1"),
        tree="not-fernet-ciphertext",
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    drive = FakeDrive(tree)
    service = _service(drive, FakeSession())

    cached = asyncio.run(service.get_drive_tree(_db(db), DriveId("drive-1")))

    assert "DELETE FROM user_drive_tree_cache" in str(db.executed[-1])
    assert "user_drive_tree_cache.xmin" in str(db.executed[-1])
    assert drive.walks == 1
    assert cached.tree == tree
    assert cached.refreshed_at is None


def test_save_tree_stores_ciphertext_and_purges_stale_rows() -> None:
    tree = _tree()
    save_session = FakeSession()
    user = _user()
    service = _service(FakeDrive(tree), save_session, user=user)

    asyncio.run(service.refresh_drive(_db(FakeSession()), DriveId("drive-1")))

    upsert, purge = save_session.executed
    upsert_params = upsert.compile().params
    assert upsert_params is not None
    stored = upsert_params["tree"]
    assert stored != tree.model_dump_json()
    assert DriveTree.model_validate_json(decrypt_cache(stored)) == tree

    purge_sql = str(purge.compile())
    assert "DELETE FROM user_drive_tree_cache" in purge_sql
    assert "refreshed_at <" in purge_sql
    assert upsert_params["user_id"] == user.id


def test_invalidation_removes_every_principals_row_for_drive() -> None:
    db = FakeSession()
    service = _service(FakeDrive(_tree()), FakeSession())

    asyncio.run(service.invalidate_drive(_db(db), DriveId("drive-1")))

    compiled = db.executed[0].compile()
    sql = str(compiled)
    assert "DELETE FROM user_drive_tree_cache" in sql
    assert "user_drive_tree_cache.drive_id" in sql
    assert "user_id" not in sql
    assert db.commits == 1


def test_decoded_tree_cache_is_scoped_by_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    tree = _tree()
    refreshed_at = datetime.now(UTC)
    row = DriveTreeCache(
        user_id=UserId(uuid.uuid7()),
        drive_id=DriveId("drive-principal-cache-test"),
        tree=encrypt_cache(tree.model_dump_json()),
        refreshed_at=refreshed_at,
    )
    first = _service(FakeDrive(tree), FakeSession(), user=_user())
    second = _service(FakeDrive(tree), FakeSession(), user=_user())
    decode_calls = 0
    original_decode = sidebar_cache._decrypt_and_parse

    def counting_decode(blob: str, decoder: MultiFernet) -> DriveTree:
        nonlocal decode_calls
        decode_calls += 1
        return original_decode(blob, decoder)

    monkeypatch.setattr(sidebar_cache, "_decrypt_and_parse", counting_decode)

    asyncio.run(first.peek_drive_tree(_db(FakeSession(row=row)), row.drive_id))
    asyncio.run(first.peek_drive_tree(_db(FakeSession(row=row)), row.drive_id))
    asyncio.run(second.peek_drive_tree(_db(FakeSession(row=row)), row.drive_id))

    assert decode_calls == 2


async def test_concurrent_cold_tree_reads_decode_once() -> None:
    user = _user()
    tree = _tree()
    row = DriveTreeCache(
        user_id=user.id,
        drive_id=DriveId("decode-concurrent-test"),
        tree=encrypt_cache(tree.model_dump_json()),
        refreshed_at=datetime.now(UTC),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class CountingCache(PostgresSidebarCacheService):
        @override
        async def _decode_tree(self, blob: str, decoder: MultiFernet) -> DriveTree:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return await super()._decode_tree(blob, decoder)

    services = [
        CountingCache(
            drive=FakeDrive(tree),
            user=user,
            singleflight=Singleflight(),
            bg_tasks=set(),
            sessionmaker=dependency(None),
        )
        for _ in range(6)
    ]
    sessions = [FakeSession(row=row) for _ in services]
    tasks = [
        asyncio.create_task(service.peek_drive_tree(_db(db), row.drive_id))
        for service, db in zip(services, sessions, strict=True)
    ]
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0)
    finally:
        release.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1
    first = results[0]
    assert first is not None
    assert all(result is not None and result.tree is first.tree for result in results)
    assert all(db.commits == 1 for db in sessions)


def test_decoded_cache_bounds_total_nodes_and_skips_oversized_trees() -> None:
    cache = sidebar_cache._DecodedTreeCache(
        max_entries=32, max_nodes=3, max_payload_bytes=32 * 1024 * 1024
    )
    user = _user()
    version = sidebar_cache._TreeVersion(datetime.now(UTC), 0, "1", "(0,1)")
    decoder = object()
    keys = [(UserId(user.id), DriveId(f"budget-{index}"), version, decoder) for index in range(3)]
    node = _tree().folders[0]
    tree = DriveTree(
        drive_id=DriveId("budget"), folders=[node, node], files=[], files_complete=True
    )
    payload_bytes = len(encrypt_cache(tree.model_dump_json()))
    cache.put(keys[0], tree, payload_bytes=payload_bytes)
    cache.put(keys[1], tree, payload_bytes=payload_bytes)

    assert cache.get(keys[0]) is None
    assert cache.get(keys[1]) is tree
    oversized = DriveTree(
        drive_id=DriveId("large-budget"), folders=[node] * 4, files=[], files_complete=True
    )
    cache.put(keys[2], oversized, payload_bytes=len(encrypt_cache(oversized.model_dump_json())))
    assert cache.get(keys[2]) is None
    assert cache.get(keys[1]) is tree


@dataclass(frozen=True)
class _SingleflightResults:
    first: CachedDriveTree
    second: CachedDriveTree


def test_singleflight_keys_are_scoped_by_principal() -> None:
    async def exercise() -> _SingleflightResults:
        shared: Singleflight[sidebar_cache.SidebarFlightResult] = Singleflight()
        started = asyncio.Event()
        release = asyncio.Event()
        first_tree = _tree()
        second_tree = _tree().model_copy(
            update={"folders": [_tree().folders[0].model_copy(update={"name": "Other principal"})]}
        )
        first = _service(
            BlockingDrive(first_tree, started, release),
            FakeSession(),
            user=_user(),
            singleflight=shared,
        )
        second = _service(
            FakeDrive(second_tree),
            FakeSession(),
            user=_user(),
            singleflight=shared,
        )
        first_task = asyncio.create_task(
            first.get_drive_tree(_db(FakeSession()), DriveId("drive-1"))
        )
        await started.wait()
        second_result = await asyncio.wait_for(
            second.get_drive_tree(_db(FakeSession()), DriveId("drive-1")),
            timeout=0.25,
        )
        release.set()
        return _SingleflightResults(first=await first_task, second=second_result)

    results = asyncio.run(exercise())

    assert results.first.tree.folders[0].name == "Quarterly Plans"
    assert results.second.tree.folders[0].name == "Other principal"


def test_unparseable_row_is_treated_as_cache_miss() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id=DriveId("drive-1"),
        tree=encrypt_cache('{"drive_id": "drive-1"}'),
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    drive = FakeDrive(tree)
    service = _service(drive, FakeSession())

    cached = asyncio.run(service.get_drive_tree(_db(db), DriveId("drive-1")))

    assert "DELETE FROM user_drive_tree_cache" in str(db.executed[-1])
    assert drive.walks == 1
    assert cached.tree == tree
    assert cached.refreshed_at is None


def test_pre_files_complete_row_is_treated_as_cache_miss() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id=DriveId("drive-1"),
        tree=encrypt_cache('{"drive_id": "drive-1", "folders": [], "files": []}'),
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    drive = FakeDrive(tree)
    service = _service(drive, FakeSession())

    cached = asyncio.run(service.get_drive_tree(_db(db), DriveId("drive-1")))

    assert "DELETE FROM user_drive_tree_cache" in str(db.executed[-1])
    assert drive.walks == 1
    assert cached.tree == tree


def test_cache_max_age_is_long_enough_to_matter() -> None:
    assert timedelta(days=7) <= CACHE_MAX_AGE
    assert CACHE_MAX_AGE > settings.SIDEBAR_CACHE_TTL


def test_peek_returns_warm_row_without_walking() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id=DriveId("drive-1"),
        tree=encrypt_cache(tree.model_dump_json()),
        refreshed_at=datetime.now(UTC),
    )
    drive = FakeDrive(tree)
    service = _service(drive, FakeSession())

    cached = asyncio.run(service.peek_drive_tree(_db(FakeSession(row=row)), DriveId("drive-1")))

    assert cached is not None
    assert cached.tree == tree
    assert drive.walks == 0


def test_peek_cold_cache_returns_none_and_never_walks() -> None:
    drive = FakeDrive(_tree())
    service = _service(drive, FakeSession())

    cached = asyncio.run(service.peek_drive_tree(_db(FakeSession(row=None)), DriveId("drive-1")))

    assert cached is None
    assert drive.walks == 0


def test_byte_tree_crypto_preserves_legacy_json_and_model_field_exclusions() -> None:
    original = _tree()
    folder = original.folders[0].model_copy(
        update={
            "name": "Résumé 📚 知識",
            "description": "نص محفوظ",
            "version": "excluded-provider-version",
            "drive_id": DriveId("excluded-provider-drive"),
        }
    )
    tree = original.model_copy(update={"folders": [folder]})
    legacy_json = tree.model_dump_json()
    decoder = cache_decoder_identity()
    encrypted = sidebar_cache._encrypt_tree(tree)

    assert decoder.decrypt(encrypted.encode()) == legacy_json.encode()
    assert "excluded-provider" not in legacy_json
    assert sidebar_cache._decrypt_and_parse(encrypted, decoder) == DriveTree.model_validate_json(
        legacy_json
    )
