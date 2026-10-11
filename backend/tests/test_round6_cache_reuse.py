from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from threading import get_ident
from types import TracebackType
from typing import NamedTuple, Self

from cryptography.fernet import Fernet
from pydantic import JsonValue
import pytest
from sqlalchemy import Select
from sqlalchemy.sql import ClauseElement

from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from tests.support import dependency


class _Result:
    def __init__(self, rows: list[tuple[str, object]]) -> None:
        self.rows = rows

    def all(self) -> list[tuple[str, object]]:
        return self.rows


class _Execution(NamedTuple):
    statement: ClauseElement
    parameters: dict[str, object]
    thread: int


class _Session:
    def __init__(self) -> None:
        self.rows: list[tuple[str, object]] = []
        self.executions: list[_Execution] = []

    async def execute(self, statement: ClauseElement, parameters: dict[str, object]) -> _Result:
        self.executions.append(_Execution(statement, dict(parameters), get_ident()))
        return _Result(self.rows)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def begin(self) -> Self:
        return self


class _KeysProtector:
    def __init__(self) -> None:
        self.lookup_threads: set[int] = set()

    def physical_keys_many(
        self,
        namespace: str,
        logical_keys: Sequence[str],
        *,
        include_rotation: bool,
    ) -> dict[str, tuple[str, ...]]:
        self.lookup_threads.add(get_ident())
        return {
            key: (f"h1.{namespace}:{key}", f"h1.older:{key}")
            if include_rotation
            else (f"h1.{namespace}:{key}",)
            for key in logical_keys
        }

    def protect(self, _namespace: str, _logical_key: str, _value: JsonValue) -> str:
        return "f1.test-envelope"

    def unprotect(self, _namespace: str, _logical_key: str, envelope: object) -> JsonValue:
        assert isinstance(envelope, str)
        return envelope


@pytest.mark.parametrize("count", [2, 4_096])
async def test_bulk_read_shape_reuses_only_sql_and_binds_current_namespace_and_keys(
    count: int,
) -> None:
    session = _Session()
    protector = _KeysProtector()
    service = CachePSqlService(dependency(lambda: session), dependency(protector))
    first_keys = [f"first-{index}" for index in range(count)]
    second_keys = [f"second-{index}" for index in range(count)]
    await service.get_many(CacheNamespace.DRIVE_LIST, first_keys)
    await service.get_many(CacheNamespace.SHORTCUT_PROBE, second_keys)
    first, second = session.executions
    assert first.statement is second.statement
    assert first.parameters["cache_namespace"] == CacheNamespace.DRIVE_LIST
    assert second.parameters["cache_namespace"] == CacheNamespace.SHORTCUT_PROBE
    assert first.parameters["cache_keys"] != second.parameters["cache_keys"]
    assert isinstance(first.statement, Select)
    assert "cache_entries.expires_at > now()" in str(first.statement)
    assert all("first" not in key for key in dependency(second.parameters["cache_keys"]))
    assert all(execution.thread == get_ident() for execution in session.executions)
    assert bool(get_ident() in protector.lookup_threads) is (count < 256)


async def test_cached_array_write_keeps_chunk_pairing_lock_order_and_fresh_ttl() -> None:
    session = _Session()
    service = CachePSqlService(dependency(lambda: session), dependency(_KeysProtector()))
    values: dict[str, JsonValue] = {f"key-{index}": index for index in range(2_001)}
    await service.put_many(CacheNamespace.DRIVE_LIST, values, ttl=timedelta(seconds=5))
    await service.put_many(
        CacheNamespace.SHORTCUT_PROBE,
        {f"other-{index}": None for index in range(8)},
        ttl=timedelta(seconds=17),
    )
    first, second, tail, later = session.executions
    assert first.statement is later.statement
    assert "DELETE FROM cache_entries" in str(first.statement)
    assert "DELETE FROM cache_entries" not in str(second.statement)
    keys = dependency(first.parameters["cache_keys"]) + dependency(second.parameters["cache_keys"])
    assert keys == sorted(keys)
    assert len(dependency(first.parameters["cache_keys"])) == len(
        dependency(first.parameters["cache_envelopes"])
    )
    assert len(dependency(second.parameters["cache_keys"])) == len(
        dependency(second.parameters["cache_envelopes"])
    )
    assert first.parameters["cache_ttl"] == timedelta(seconds=5)
    assert later.parameters["cache_ttl"] == timedelta(seconds=17)
    assert later.parameters["cache_namespace"] == CacheNamespace.SHORTCUT_PROBE
    assert "VALUES" in str(tail.statement)
    assert "DELETE FROM cache_entries" not in str(tail.statement)


async def test_namespace_seeds_preserve_rotation_and_unicode_under_threaded_reads() -> None:
    keyset = tuple(Fernet.generate_key().decode() for _ in range(3))
    protector = CacheEntryProtector(keyset)
    independent = [CacheEntryProtector((key,)) for key in keyset]
    logical_keys = ["same", "絵📚", "a:b", "ab:c"]
    namespaces = ["drive.list", "絵📚", *[f"eviction-{index}" for index in range(20)]]
    expected = {
        (namespace, logical_key): tuple(
            individual.physical_keys(namespace, logical_key)[0] for individual in independent
        )
        for namespace in namespaces
        for logical_key in logical_keys
    }
    observed = await asyncio.gather(
        *(
            asyncio.to_thread(protector.physical_keys, namespace, logical_key)
            for namespace, logical_key in expected
        )
    )
    assert observed == list(expected.values())
    assert protector.physical_keys("drive.list", "絵📚") == expected["drive.list", "絵📚"]
    assert protector.physical_keys("絵📚", "same") != protector.physical_keys("drive.list", "same")
    original = independent[-1].protect("drive.list", "same", {"old": True})
    assert protector.unprotect("drive.list", "same", original) == {"old": True}
    rotated = CacheEntryProtector(tuple(reversed(keyset)))
    assert rotated.physical_keys("drive.list", "same") == tuple(
        reversed(protector.physical_keys("drive.list", "same"))
    )
