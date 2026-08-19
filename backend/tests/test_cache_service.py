from __future__ import annotations

import asyncio
from datetime import timedelta
from types import TracebackType
from typing import NamedTuple, Self, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache.service import CacheNamespace, CachePSqlService


class _CacheRow(NamedTuple):
    key: str
    value: object


class _Result:
    def __init__(self, *, rows: list[_CacheRow] | None = None, scalar: object = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def all(self) -> list[_CacheRow]:
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

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def begin(self) -> _Transaction:
        return _Transaction(self)


class _Transaction:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self._session.commits += 1


class _SessionMaker:
    def __init__(self, *sessions: _Session) -> None:
        self._sessions = iter(sessions)
        self.calls = 0

    def __call__(self) -> _Session:
        self.calls += 1
        return next(self._sessions)


def _service(session: _Session) -> CachePSqlService:
    factory = _SessionMaker(session)
    return CachePSqlService(cast("async_sessionmaker[AsyncSession]", factory))


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
    session = _Session(_Result(rows=[_CacheRow("a", {"accessible": True}), _CacheRow("b", None)]))

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


async def test_concurrent_reads_use_independent_sessions() -> None:
    first = _Session(_Result(scalar={"name": "First"}))
    second = _Session(_Result(scalar={"name": "Second"}))
    factory = _SessionMaker(first, second)
    service = CachePSqlService(
        cast("async_sessionmaker[AsyncSession]", factory),
    )

    values = await asyncio.gather(
        service.get(CacheNamespace.DRIVE_LIST, "user-1"),
        service.get(CacheNamespace.DRIVE_LIST, "user-2"),
    )

    assert values == [{"name": "First"}, {"name": "Second"}]
    assert factory.calls == 2
