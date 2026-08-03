from __future__ import annotations

from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.service import CacheNamespace, CachePSqlService


class _Result:
    def __init__(
        self, *, rows: list[tuple[str, object]] | None = None, scalar: object = None
    ) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list[tuple[str, object]]:
        return self._rows

    def scalar_one_or_none(self) -> object:
        return self._scalar


class _Session:
    def __init__(self, *results: _Result) -> None:
        self._results = iter(results)
        self.commits = 0
        self.statements: list[object] = []

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return next(self._results, _Result())

    async def commit(self) -> None:
        self.commits += 1


def _service(session: _Session) -> CachePSqlService:
    return CachePSqlService(cast("AsyncSession", session))


async def test_put_many_is_one_atomic_expiring_upsert() -> None:
    session = _Session()

    await _service(session).put_many(
        CacheNamespace.SHORTCUT_PROBE,
        {"a": {"accessible": True}, "b": None},
        ttl=timedelta(minutes=5),
    )

    assert session.commits == 1
    assert len(session.statements) == 1
    sql = str(session.statements[0])
    assert "DELETE FROM cache_entries" in sql
    assert "INSERT INTO cache_entries" in sql
    assert "ON CONFLICT" in sql


async def test_get_ignores_expired_entries_in_sql() -> None:
    session = _Session(_Result(scalar={"name": "Workspace"}))

    value = await _service(session).get(CacheNamespace.DRIVE_LIST, "user-1")

    assert value == {"name": "Workspace"}
    assert "cache_entries.expires_at > now()" in str(session.statements[0])
    assert session.commits == 0


async def test_get_many_returns_valid_json_values() -> None:
    session = _Session(_Result(rows=[("a", {"accessible": True}), ("b", None)]))

    values = await _service(session).get_many(CacheNamespace.SHORTCUT_PROBE, ["a", "b"])

    assert values == {"a": {"accessible": True}, "b": None}


async def test_empty_put_and_get_many_skip_database() -> None:
    session = _Session()
    service = _service(session)

    await service.put_many(CacheNamespace.TEMPLATE_CATALOG, {}, ttl=timedelta(seconds=30))
    values = await service.get_many(CacheNamespace.TEMPLATE_CATALOG, [])

    assert values == {}
    assert session.statements == []
    assert session.commits == 0


async def test_non_positive_ttl_is_rejected() -> None:
    session = _Session()

    with pytest.raises(ValueError, match="ttl must be positive"):
        await _service(session).put(
            CacheNamespace.DRIVE_LIST,
            "user-1",
            [],
            ttl=timedelta(0),
        )

    assert session.statements == []
