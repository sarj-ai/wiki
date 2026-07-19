"""Public link-preview metadata coverage.

Categories: cached-tree name lookup (hit across drives, shortcut targets,
miss, corrupt-row skip, read-only guarantee, decode-LRU reuse and blob-fetch
amplification limits) and the unauthenticated /link-meta endpoint (no-cookie
200s, null on miss).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any, cast

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption import encrypt_cache
from app.database import get_db
from app.drive.schemas import DriveNode, DriveTree
from app.main import app
from app.models.wiki import DriveTreeCache
from app.sidebar.cache import _decoded_trees, _undecodable_keys, find_cached_file_name


DOC_ID = "1AbC_dEf-123"


def _node(file_id: str, name: str, *, shortcut_target_id: str | None = None) -> DriveNode:
    return DriveNode(
        id=file_id,
        name=name,
        mime_type="application/vnd.google-apps.document",
        parent_id=None,
        modified_time=None,
        shortcut_target_id=shortcut_target_id,
    )


def _row(drive_id: str, files: list[DriveNode]) -> DriveTreeCache:
    tree = DriveTree(drive_id=drive_id, folders=[], files=files, files_complete=True)
    return DriveTreeCache(
        drive_id=drive_id,
        tree=encrypt_cache(tree.model_dump_json()),
        refreshed_at=datetime.now(UTC),
    )


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0][0] if self._rows else None


class _FakeDb:
    """Serves the two-phase lookup: a keys query, then per-drive blob queries."""

    def __init__(self, rows: list[DriveTreeCache]) -> None:
        self._rows = {r.drive_id: r for r in rows}
        self.blob_fetches = 0
        self.deletes = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        columns = [c.key for c in stmt.selected_columns]
        if columns == ["drive_id", "refreshed_at"]:
            return _FakeResult([(r.drive_id, r.refreshed_at) for r in self._rows.values()])
        assert columns == ["tree"]
        self.blob_fetches += 1
        drive_id = next(iter(stmt.compile().params.values()))
        row = self._rows.get(drive_id)
        return _FakeResult([] if row is None else [(row.tree,)])

    async def delete(self, _row: object) -> None:
        self.deletes += 1


def _db(rows: list[DriveTreeCache]) -> AsyncSession:
    return cast("AsyncSession", _FakeDb(rows))


@pytest.fixture(autouse=True)
def _clear_lookup_state() -> Iterator[None]:
    _decoded_trees.clear()
    _undecodable_keys.clear()
    yield
    _decoded_trees.clear()
    _undecodable_keys.clear()


async def test_finds_name_across_drives() -> None:
    rows = [
        _row("drive-1", [_node("other-doc", "Other")]),
        _row("drive-2", [_node(DOC_ID, "Quarterly Plan")]),
    ]
    assert await find_cached_file_name(_db(rows), DOC_ID) == "Quarterly Plan"


async def test_finds_name_via_shortcut_target() -> None:
    shortcut = _node("shortcut-1", "Plan (shortcut)", shortcut_target_id=DOC_ID)
    rows = [_row("drive-1", [shortcut])]
    assert await find_cached_file_name(_db(rows), DOC_ID) == "Plan (shortcut)"


async def test_miss_returns_none() -> None:
    rows = [_row("drive-1", [_node("other-doc", "Other")])]
    assert await find_cached_file_name(_db(rows), DOC_ID) is None


async def test_empty_cache_returns_none() -> None:
    assert await find_cached_file_name(_db([]), DOC_ID) is None


async def test_repeat_lookups_reuse_decode_cache() -> None:
    fake = _FakeDb([_row("drive-1", [_node(DOC_ID, "Quarterly Plan")])])
    db = cast("AsyncSession", fake)
    assert await find_cached_file_name(db, DOC_ID) == "Quarterly Plan"
    assert fake.blob_fetches == 1
    assert await find_cached_file_name(db, DOC_ID) == "Quarterly Plan"
    assert fake.blob_fetches == 1


async def test_corrupt_row_is_skipped_not_deleted_and_not_refetched() -> None:
    corrupt = DriveTreeCache(
        drive_id="drive-corrupt",
        tree="not-a-fernet-token",
        refreshed_at=datetime.now(UTC),
    )
    good = _row("drive-2", [_node(DOC_ID, "Quarterly Plan")])
    fake = _FakeDb([corrupt, good])
    db = cast("AsyncSession", fake)
    assert await find_cached_file_name(db, DOC_ID) == "Quarterly Plan"
    assert fake.deletes == 0
    fetches_after_first = fake.blob_fetches
    assert await find_cached_file_name(db, DOC_ID) == "Quarterly Plan"
    assert fake.blob_fetches == fetches_after_first


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _client(rows: list[DriveTreeCache]) -> AsyncClient:
    async def _fake_db() -> AsyncIterator[AsyncSession]:
        yield _db(rows)

    app.dependency_overrides[get_db] = _fake_db
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_endpoint_returns_name_without_auth() -> None:
    client = _client([_row("drive-1", [_node(DOC_ID, "Quarterly Plan")])])
    async with client:
        resp = await client.get(f"/link-meta/{DOC_ID}")
    assert resp.status_code == 200
    assert resp.json() == {"name": "Quarterly Plan"}


async def test_endpoint_null_on_miss() -> None:
    client = _client([])
    async with client:
        resp = await client.get("/link-meta/unknown-doc")
    assert resp.status_code == 200
    assert resp.json() == {"name": None}
