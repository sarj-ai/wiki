from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from threading import get_ident
from types import TracebackType
from typing import NamedTuple, Self

from cryptography.fernet import Fernet
from pydantic import JsonValue, ValidationError
import pytest
from sqlalchemy import Select
from sqlalchemy.sql import ClauseElement

from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, CachePSqlService
from tests.support import dependency


class _CacheRow(NamedTuple):
    key: str
    value: object


class _Result:
    def __init__(self, *, rows: list[_CacheRow] | None = None) -> None:
        self._rows = rows or []

    def all(self) -> list[_CacheRow]:
        return self._rows


class _Session:
    def __init__(self, *results: _Result) -> None:
        self._results = iter(results)
        self.commits = 0
        self.statements: list[object] = []

    async def execute(
        self, statement: object, parameters: dict[str, object] | None = None
    ) -> _Result:
        self.statements.append(
            statement.params(parameters)
            if isinstance(statement, Select) and parameters is not None
            else statement
        )
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


def _protector(*keys: str) -> CacheEntryProtector:
    return CacheEntryProtector(tuple(keys) or (Fernet.generate_key().decode(),))


def _service(*sessions: _Session, protector: CacheEntryProtector | None = None) -> CachePSqlService:
    factory = _SessionMaker(*sessions)
    return CachePSqlService(
        dependency(factory),
        protector or _protector(),
    )


class _CollidingProtector:
    def physical_keys(self, namespace: str, logical_key: str) -> tuple[str, ...]:
        return ("h1.collision",)

    def physical_keys_many(
        self, namespace: str, logical_keys: Sequence[str], *, include_rotation: bool
    ) -> dict[str, tuple[str, ...]]:
        return dict.fromkeys(logical_keys, ("h1.collision",))

    def protect(self, namespace: str, logical_key: str, value: object) -> str:
        raise AssertionError


def _statement_parameters(statement: object) -> str:
    assert isinstance(statement, ClauseElement)
    return repr(statement.compile().params)


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
    protector = _protector()
    physical_key = protector.physical_keys(CacheNamespace.DRIVE_LIST, "user-1")[0]
    session = _Session(
        _Result(
            rows=[
                _CacheRow(
                    physical_key,
                    protector.protect(CacheNamespace.DRIVE_LIST, "user-1", {"name": "Workspace"}),
                )
            ]
        )
    )

    value = await _service(session, protector=protector).get(CacheNamespace.DRIVE_LIST, "user-1")

    assert value == {"name": "Workspace"}
    assert "cache_entries.expires_at > now()" in str(session.statements[0])
    assert session.commits == 0


async def test_get_many_returns_valid_json_values() -> None:
    protector = _protector()
    a = protector.physical_keys(CacheNamespace.SHORTCUT_PROBE, "a")[0]
    b = protector.physical_keys(CacheNamespace.SHORTCUT_PROBE, "b")[0]
    session = _Session(
        _Result(
            rows=[
                _CacheRow(
                    a,
                    protector.protect(
                        CacheNamespace.SHORTCUT_PROBE,
                        "a",
                        {"accessible": True},
                    ),
                ),
                _CacheRow(b, protector.protect(CacheNamespace.SHORTCUT_PROBE, "b", None)),
            ]
        )
    )

    values = await _service(session, protector=protector).get_many(
        CacheNamespace.SHORTCUT_PROBE,
        ["a", "b"],
    )

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


async def test_ttl_longer_than_five_minutes_is_rejected() -> None:
    session = _Session()

    with pytest.raises(ValueError, match="must not exceed five minutes"):
        await _service(session).put(
            CacheNamespace.DRIVE_LIST,
            "user-1",
            [],
            ttl=timedelta(minutes=5, microseconds=1),
        )

    assert session.statements == []


async def test_concurrent_reads_use_independent_sessions() -> None:
    protector = _protector()
    first_key = protector.physical_keys(CacheNamespace.DRIVE_LIST, "user-1")[0]
    second_key = protector.physical_keys(CacheNamespace.DRIVE_LIST, "user-2")[0]
    first = _Session(
        _Result(
            rows=[
                _CacheRow(
                    first_key,
                    protector.protect(CacheNamespace.DRIVE_LIST, "user-1", {"name": "First"}),
                )
            ]
        )
    )
    second = _Session(
        _Result(
            rows=[
                _CacheRow(
                    second_key,
                    protector.protect(CacheNamespace.DRIVE_LIST, "user-2", {"name": "Second"}),
                )
            ]
        )
    )
    factory = _SessionMaker(first, second)
    service = CachePSqlService(
        dependency(factory),
        protector,
    )

    values = await asyncio.gather(
        service.get(CacheNamespace.DRIVE_LIST, "user-1"),
        service.get(CacheNamespace.DRIVE_LIST, "user-2"),
    )

    assert values[0] == {"name": "First"}
    assert values[1] == {"name": "Second"}
    assert factory.calls == 2


async def test_read_never_queries_or_returns_legacy_plaintext_key() -> None:
    read = _Session(_Result(rows=[]))

    value = await _service(read).get(CacheNamespace.DRIVE_LIST, "legacy-user-1")

    assert value is None
    assert "legacy-user-1" not in _statement_parameters(read.statements[0])


async def test_corrupt_newest_entry_falls_through_to_valid_older_candidate() -> None:
    new_key, old_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    protector = _protector(new_key, old_key)
    newest, older = protector.physical_keys(CacheNamespace.DRIVE_LIST, "user-1")
    old_protector = _protector(old_key)
    read = _Session(
        _Result(
            rows=[
                _CacheRow(newest, "f1.corrupt"),
                _CacheRow(
                    older,
                    old_protector.protect(
                        CacheNamespace.DRIVE_LIST,
                        "user-1",
                        {"secret": "older"},
                    ),
                ),
            ]
        )
    )
    value = await _service(read, protector=protector).get(
        CacheNamespace.DRIVE_LIST,
        "user-1",
    )

    assert value == {"secret": "older"}
    assert len(read.statements) == 1


async def test_rotation_prefers_newest_row_and_reads_old_row_when_newest_is_absent() -> None:
    new_key, old_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    protector = _protector(new_key, old_key)
    newest, older = protector.physical_keys(CacheNamespace.DRIVE_LIST, "user-1")
    new_value = protector.protect(
        CacheNamespace.DRIVE_LIST,
        "user-1",
        {"generation": "new"},
    )
    old_value = _protector(old_key).protect(
        CacheNamespace.DRIVE_LIST,
        "user-1",
        {"generation": "old"},
    )
    newest_read = _Session(
        _Result(rows=[_CacheRow(older, old_value), _CacheRow(newest, new_value)])
    )
    old_read = _Session(_Result(rows=[_CacheRow(older, old_value)]))
    service = _service(newest_read, old_read, protector=protector)

    assert await service.get(CacheNamespace.DRIVE_LIST, "user-1") == {"generation": "new"}
    assert await service.get(CacheNamespace.DRIVE_LIST, "user-1") == {"generation": "old"}


async def test_delete_removes_only_hmac_rotation_candidates() -> None:
    session = _Session()
    protector = _protector(Fernet.generate_key().decode(), Fernet.generate_key().decode())

    await _service(session, protector=protector).delete(CacheNamespace.DRIVE_LIST, "user-1")

    rendered_parameters = _statement_parameters(session.statements[0])
    assert "user-1" not in rendered_parameters
    for physical_key in protector.physical_keys(CacheNamespace.DRIVE_LIST, "user-1"):
        assert physical_key in rendered_parameters


async def test_put_persists_only_active_hmac_key_and_encrypted_envelope() -> None:
    session = _Session()
    protector = _protector()

    await _service(session, protector=protector).put(
        CacheNamespace.DRIVE_LIST,
        "private-user-id",
        {"drive": "private-drive-id"},
        ttl=timedelta(minutes=1),
    )

    parameters = _statement_parameters(session.statements[0])
    assert "private-user-id" not in parameters
    assert "private-drive-id" not in parameters
    assert protector.physical_keys(CacheNamespace.DRIVE_LIST, "private-user-id")[0] in parameters
    assert "f1" in parameters
    assert "cache_entries.key !~" in str(session.statements[0])
    assert "^h[0-9]+\\\\." in parameters


async def test_get_many_physical_key_collision_fails_closed_without_database_access() -> None:
    session = _Session()
    service = _service(
        session,
        protector=dependency(_CollidingProtector()),
    )

    values = await service.get_many(CacheNamespace.DRIVE_LIST, ["user-a", "user-b"])

    assert values == {}
    assert session.statements == []


async def test_put_many_physical_key_collision_fails_before_database_access() -> None:
    session = _Session()
    service = _service(
        session,
        protector=dependency(_CollidingProtector()),
    )

    await service.put_many(
        CacheNamespace.DRIVE_LIST,
        {"user-a": [], "user-b": []},
        ttl=timedelta(minutes=1),
    )

    assert session.statements == []


async def test_get_many_chunks_large_lookup_batches() -> None:
    first, second = _Session(_Result()), _Session(_Result())
    factory = _SessionMaker(first, second)
    service = CachePSqlService(
        dependency(factory),
        _protector(),
    )

    values = await service.get_many(
        CacheNamespace.SHORTCUT_PROBE,
        [f"key-{index}" for index in range(10_001)],
    )

    assert values == {}
    assert factory.calls == 2
    assert len(first.statements) == 1
    assert len(second.statements) == 1


class _ObservedProtector:
    def __init__(self) -> None:
        self.wrapped = _protector()
        self.lookup_keys: list[str] = []
        self.lookup_threads: set[int] = set()
        self.protection_threads: set[int] = set()
        self.unprotection_threads: set[int] = set()

    def physical_keys(self, namespace: str, logical_key: str) -> tuple[str, ...]:
        self.lookup_keys.append(logical_key)
        self.lookup_threads.add(get_ident())
        return self.wrapped.physical_keys(namespace, logical_key)

    def physical_keys_many(
        self, namespace: str, logical_keys: Sequence[str], *, include_rotation: bool
    ) -> dict[str, tuple[str, ...]]:
        self.lookup_keys.extend(logical_keys)
        self.lookup_threads.add(get_ident())
        return self.wrapped.physical_keys_many(
            namespace,
            logical_keys,
            include_rotation=include_rotation,
        )

    def protect(self, namespace: str, logical_key: str, value: JsonValue) -> str:
        self.protection_threads.add(get_ident())
        return self.wrapped.protect(namespace, logical_key, value)

    def unprotect(self, namespace: str, logical_key: str, envelope: object) -> JsonValue:
        self.unprotection_threads.add(get_ident())
        return self.wrapped.unprotect(namespace, logical_key, envelope)


async def test_duplicate_lookup_keys_are_hashed_once() -> None:
    protector = _ObservedProtector()
    values = await _service(_Session(_Result()), protector=dependency(protector)).get_many(
        CacheNamespace.SHORTCUT_PROBE, ["same"] * 1000
    )
    assert values == {}
    assert protector.lookup_keys == ["same"]
    assert protector.lookup_threads == {get_ident()}


async def test_large_cache_batch_crypto_does_not_run_on_the_request_thread() -> None:
    protector = _ObservedProtector()
    namespace = CacheNamespace.SHORTCUT_PROBE
    values: dict[str, JsonValue] = {f"bulk-{index}": {"accessible": True} for index in range(1000)}
    rows = [
        _CacheRow(
            protector.wrapped.physical_keys(namespace, key)[0],
            protector.wrapped.protect(namespace, key, value),
        )
        for key, value in values.items()
    ]
    read, write = _Session(_Result(rows=rows)), _Session()
    service = _service(read, write, protector=dependency(protector))

    assert await service.get_many(namespace, list(values)) == values
    await service.put_many(namespace, values, ttl=timedelta(minutes=1))

    assert get_ident() not in protector.lookup_threads
    assert get_ident() not in protector.protection_threads
    assert get_ident() not in protector.unprotection_threads
    assert protector.lookup_threads
    assert protector.protection_threads
    assert protector.unprotection_threads
    assert write.commits == 1


@pytest.mark.parametrize("many", [False, True])
async def test_large_cache_values_offload_crypto_even_with_few_keys(many: bool) -> None:
    protector = _ObservedProtector()
    namespace = CacheNamespace.TEMPLATE_CATALOG
    values: dict[str, JsonValue] = {"catalog": {"content": "x" * (512 * 1024)}}
    if many:
        values["catalog-other"] = {"content": "y" * (512 * 1024)}
    rows = [
        _CacheRow(
            protector.wrapped.physical_keys(namespace, key)[0],
            protector.wrapped.protect(namespace, key, value),
        )
        for key, value in values.items()
    ]
    service = _service(_Session(_Result(rows=rows)), protector=dependency(protector))

    if many:
        assert await service.get_many(namespace, list(values)) == values
    else:
        assert await service.get(namespace, "catalog") == values["catalog"]

    assert protector.lookup_threads == {get_ident()}
    assert protector.unprotection_threads
    assert get_ident() not in protector.unprotection_threads


@pytest.mark.parametrize("many", [False, True])
async def test_small_cache_hits_keep_crypto_inline(many: bool) -> None:
    protector = _ObservedProtector()
    namespace = CacheNamespace.TEMPLATE_CATALOG
    value: JsonValue = {"content": "x" * 1024}
    row = _CacheRow(
        protector.wrapped.physical_keys(namespace, "catalog")[0],
        protector.wrapped.protect(namespace, "catalog", value),
    )
    service = _service(_Session(_Result(rows=[row])), protector=dependency(protector))

    if many:
        assert await service.get_many(namespace, ["catalog"]) == {"catalog": value}
    else:
        assert await service.get(namespace, "catalog") == value

    assert protector.unprotection_threads == {get_ident()}


@pytest.mark.parametrize("kind", ["small", "unicode", "wide", "deep"])
async def test_single_cache_writes_select_crypto_thread_by_bounded_workload(kind: str) -> None:
    protector = _ObservedProtector()
    value: JsonValue
    match kind:
        case "unicode":
            value = {"content": "📚" * (256 * 1024)}
        case "wide":
            wide: list[JsonValue] = [0] * 80_000
            value = wide
        case "deep":
            value = "x" * (1024 * 1024)
            for _ in range(200):
                value = [value]
        case _:
            value = {"content": "x" * 1024}
    session = _Session()
    service = _service(session, protector=dependency(protector))

    await service.put(CacheNamespace.TEMPLATE_CATALOG, "catalog", value, ttl=timedelta(minutes=1))

    assert bool(get_ident() in protector.protection_threads) is (kind == "small")
    assert protector.protection_threads
    assert session.commits == 1


@pytest.mark.parametrize("kind", ["invalid", "cyclic"])
async def test_invalid_or_cyclic_cache_write_values_still_fail_before_opening_transaction(
    kind: str,
) -> None:
    value: JsonValue
    if kind == "cyclic":
        cycle: list[JsonValue] = []
        cycle.append(cycle)
        value = cycle
    else:
        value = dependency(object())
    session = _Session()

    with pytest.raises(ValidationError):
        await _service(session).put(
            CacheNamespace.TEMPLATE_CATALOG, "invalid", value, ttl=timedelta(minutes=1)
        )

    assert session.statements == []
    assert session.commits == 0
