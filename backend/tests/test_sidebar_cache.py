"""PostgresSidebarCacheService — encrypted tree storage.

Covers the Fernet round-trip (save → load), the undecryptable-row
fallback (key rotation behaves like a cache miss), and the retention
purge piggybacked on saves. All DB/Drive access is faked in-memory.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
import uuid

from sqlalchemy.dialects import postgresql

from app.auth.encryption import decrypt_text, encrypt_text
from app.config import settings
from app.drive.schemas import DriveNode, DriveTree, SearchResult, SnippetResult
from app.drive.service import DriveService
from app.models.user import User
from app.models.wiki import DriveTreeCache
from app.sidebar.cache import CACHE_MAX_AGE, PostgresSidebarCacheService
from app.sidebar.singleflight import Singleflight


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
    u.email = "test@sarj.ai"
    u.google_id = "gid"
    u.token_version = 0
    return u


def _tree() -> DriveTree:
    return DriveTree(
        drive_id="drive-1",
        folders=[
            DriveNode(
                id="folder-1",
                name="Quarterly Plans",
                mime_type="application/vnd.google-apps.folder",
                parent_id="drive-1",
                modified_time=None,
            )
        ],
        files=[],
    )


class FakeDrive(DriveService):
    def __init__(self, tree: DriveTree) -> None:
        self.tree = tree
        self.walks = 0

    async def list_drives(self) -> list[Any]:
        return []

    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        self.walks += 1
        return self.tree

    async def list_folder_children(self, folder_id: str) -> list[DriveNode]:
        return []

    async def search_drive(
        self, drive_id: str, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        return []

    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        return []

    async def fetch_snippet(
        self, file_id: str, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        raise NotImplementedError


class FakeSession:
    def __init__(self, row: DriveTreeCache | None = None) -> None:
        self.row = row
        self.deleted: list[object] = []
        self.executed: list[Any] = []
        self.commits = 0

    async def get(self, model: type[DriveTreeCache], pk: str) -> DriveTreeCache | None:
        return self.row

    async def delete(self, row: object) -> None:
        self.deleted.append(row)

    async def execute(self, stmt: Any) -> None:
        self.executed.append(stmt)

    async def commit(self) -> None:
        self.commits += 1


def _service(drive: FakeDrive, save_session: FakeSession) -> PostgresSidebarCacheService:
    @asynccontextmanager
    async def fake_sessionmaker() -> AsyncIterator[FakeSession]:
        yield save_session

    return PostgresSidebarCacheService(
        drive=drive,
        user=_user(),
        singleflight=Singleflight(),
        bg_tasks=set(),
        sessionmaker=fake_sessionmaker,  # type: ignore[arg-type]
    )


def test_warm_row_round_trips_through_encryption() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id="drive-1",
        tree=encrypt_text(tree.model_dump_json()),
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    service = _service(FakeDrive(tree), FakeSession())

    cached = asyncio.run(service.get_drive_tree(db, "drive-1"))  # type: ignore[arg-type]

    assert cached.tree == tree
    assert cached.refreshed_at == row.refreshed_at
    assert db.deleted == []


def test_undecryptable_row_is_treated_as_cache_miss() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id="drive-1",
        tree="not-fernet-ciphertext",
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    drive = FakeDrive(tree)
    service = _service(drive, FakeSession())

    cached = asyncio.run(service.get_drive_tree(db, "drive-1"))  # type: ignore[arg-type]

    # Unreadable row dropped, fresh walk served instead.
    assert db.deleted == [row]
    assert drive.walks == 1
    assert cached.tree == tree
    assert cached.refreshed_at is None


def test_save_tree_stores_ciphertext_and_purges_stale_rows() -> None:
    tree = _tree()
    save_session = FakeSession()
    service = _service(FakeDrive(tree), save_session)

    asyncio.run(service.refresh_drive(FakeSession(), "drive-1"))  # type: ignore[arg-type]

    upsert, purge = save_session.executed
    stored = upsert.compile(dialect=postgresql.dialect()).params["tree"]
    assert stored != tree.model_dump_json()  # not plaintext
    assert DriveTree.model_validate_json(decrypt_text(stored)) == tree

    purge_sql = str(purge.compile(dialect=postgresql.dialect()))
    assert "DELETE FROM drive_tree_cache" in purge_sql
    assert "refreshed_at <" in purge_sql


def test_unparseable_row_is_treated_as_cache_miss() -> None:
    tree = _tree()
    row = DriveTreeCache(
        drive_id="drive-1",
        tree=encrypt_text('{"drive_id": "drive-1"}'),
        refreshed_at=datetime.now(UTC),
    )
    db = FakeSession(row=row)
    drive = FakeDrive(tree)
    service = _service(drive, FakeSession())

    cached = asyncio.run(service.get_drive_tree(db, "drive-1"))  # type: ignore[arg-type]

    assert db.deleted == [row]
    assert drive.walks == 1
    assert cached.tree == tree
    assert cached.refreshed_at is None


def test_cache_max_age_is_long_enough_to_matter() -> None:
    assert timedelta(days=7) <= CACHE_MAX_AGE
    assert CACHE_MAX_AGE > settings.SIDEBAR_CACHE_TTL
