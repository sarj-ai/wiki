from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from functools import cache
from itertools import batched
from typing import NamedTuple, override

from pydantic import JsonValue
from sqlalchemy import (
    Interval,
    Select,
    Text,
    bindparam,
    column,
    delete,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY, Insert, insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CTE

from app.cache.protection import CacheEntryProtector, InvalidCacheEntryError
from app.config import settings
from app.models.wiki import CacheEntry
from app.performance import increment, timed


class CacheNamespace(StrEnum):
    DRIVE_LIST = "drive.list"
    SHORTCUT_PROBE = "drive.shortcut_probe"
    TEMPLATE_CATALOG = "template.catalog"


_MAX_LOOKUP_KEYS_PER_QUERY = 10_000
_MAX_WRITE_ROWS_PER_QUERY = 1_000
_ARRAY_WRITE_THRESHOLD = 8
_UNNEST_LOOKUP_THRESHOLD = 4_096
_BULK_CPU_OFFLOAD_THRESHOLD = 256
_PAYLOAD_CPU_OFFLOAD_BYTES = 512 * 1024
_WRITE_PAYLOAD_ITEM_BYTES = 16
_NON_ASCII_PAYLOAD_CHAR_BYTES = 4
_WRITE_PAYLOAD_ITEM_LIMIT = _PAYLOAD_CPU_OFFLOAD_BYTES // _WRITE_PAYLOAD_ITEM_BYTES
_MAX_CACHE_TTL = timedelta(minutes=5)


class _ProtectedCacheValue(NamedTuple):
    key: str
    envelope: str


def _needs_crypto_offload(envelopes: Iterable[JsonValue]) -> bool:
    ciphertext_bytes = 0
    for envelope in envelopes:
        if not isinstance(envelope, str):
            continue
        ciphertext_bytes += len(envelope)
        if ciphertext_bytes >= _PAYLOAD_CPU_OFFLOAD_BYTES:
            return True
    return False


def _needs_write_crypto_offload(values: Mapping[str, JsonValue]) -> bool:
    # A bounded workload estimate avoids serializing large values twice. The
    # protector still performs the authoritative JSON validation on either path.
    workload = 0
    pending: list[Iterator[JsonValue]] = [iter(values), iter(values.values())]
    while pending:
        try:
            value = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        workload += _WRITE_PAYLOAD_ITEM_BYTES
        match value:
            case dict() | list() if len(value) >= _WRITE_PAYLOAD_ITEM_LIMIT:
                return True
            case str():
                workload += len(value) * (1 if value.isascii() else _NON_ASCII_PAYLOAD_CHAR_BYTES)
            case dict():
                pending.extend((iter(value), iter(value.values())))
            case list():
                pending.append(iter(value))
            case _:
                pass
        if workload >= _PAYLOAD_CPU_OFFLOAD_BYTES:
            return True
    return False


def _cache_envelope_text() -> ColumnElement[str]:
    # Cache entries are authenticated JSONB string envelopes. Extracting their
    # scalar text skips a second JSON escaping/decoding of the ciphertext.
    # Non-string legacy values cannot have the protector's envelope prefix.
    return CacheEntry.value.op("#>>", return_type=Text)(literal([], type_=ARRAY(Text)))


@cache
def _cache_get_statement() -> Select[str, str]:
    # Reuse only the immutable statement shape. Each execution binds the
    # current namespace and rotation keys and evaluates its fresh TTL check.
    return select(CacheEntry.key, _cache_envelope_text()).where(
        CacheEntry.namespace == bindparam("cache_namespace", type_=Text),
        CacheEntry.key.in_(bindparam("cache_keys", expanding=True, type_=Text)),
        CacheEntry.expires_at > func.now(),
    )


@cache
def _cache_bulk_get_statement(*, large: bool) -> Select[str, str]:
    if not large:
        return _cache_get_statement()
    lookup = (
        func.unnest(bindparam("cache_keys", type_=ARRAY(Text)))
        .table_valued(column("key", Text))
        .render_derived()
    )
    return (
        select(CacheEntry.key, _cache_envelope_text())
        .join(lookup, CacheEntry.key == lookup.c.key)
        .where(
            CacheEntry.namespace == bindparam("cache_namespace", type_=Text),
            CacheEntry.expires_at > func.now(),
        )
    )


def _cache_insert(
    namespace: CacheNamespace,
    chunk: tuple[_ProtectedCacheValue, ...],
    expires_at: ColumnElement[datetime],
) -> Insert:
    return (
        pg_insert(CacheEntry)
        .execution_options(dml_strategy="raw")
        .values(
            [
                {
                    "namespace": namespace,
                    "key": value.key,
                    "value": value.envelope,
                    "expires_at": expires_at,
                }
                for value in chunk
            ]
        )
    )


@cache
def _cache_write_cleanup_statement() -> CTE:
    return (
        delete(CacheEntry)
        .where(
            or_(
                CacheEntry.expires_at <= func.now(),
                (CacheEntry.namespace == bindparam("cache_namespace", type_=Text))
                & CacheEntry.key.op("!~")(literal(r"^h[0-9]+\.", literal_execute=True)),
            )
        )
        .cte("delete_expired_cache_entries")
    )


@cache
def _cache_array_write_statement(*, cleanup: bool) -> Insert:
    rows = (
        func.unnest(
            bindparam("cache_keys", type_=ARRAY(Text)),
            bindparam("cache_envelopes", type_=ARRAY(Text)),
        )
        .table_valued(column("key", Text), column("envelope", Text))
        .render_derived()
    )
    insert = (
        pg_insert(CacheEntry)
        .execution_options(dml_strategy="raw")
        .from_select(
            [CacheEntry.namespace, CacheEntry.key, CacheEntry.value, CacheEntry.expires_at],
            select(
                bindparam("cache_namespace", type_=Text),
                rows.c.key,
                func.to_jsonb(rows.c.envelope),
                func.now() + bindparam("cache_ttl", type_=Interval),
            ),
        )
    )
    statement = insert.on_conflict_do_update(
        index_elements=[CacheEntry.namespace, CacheEntry.key],
        set_={"value": insert.excluded.value, "expires_at": insert.excluded.expires_at},
    )
    return statement.add_cte(_cache_write_cleanup_statement()) if cleanup else statement


class CacheService(ABC):
    @abstractmethod
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None: ...

    @abstractmethod
    async def get_many(
        self,
        namespace: CacheNamespace,
        keys: list[str],
    ) -> dict[str, JsonValue]: ...

    @abstractmethod
    async def put(
        self,
        namespace: CacheNamespace,
        key: str,
        value: JsonValue,
        *,
        ttl: timedelta,
    ) -> None: ...

    @abstractmethod
    async def put_many(
        self,
        namespace: CacheNamespace,
        values: Mapping[str, JsonValue],
        *,
        ttl: timedelta,
    ) -> None: ...

    @abstractmethod
    async def delete(self, namespace: CacheNamespace, key: str) -> None: ...


class NullCacheService(CacheService):
    @override
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None:
        return None

    @override
    async def get_many(
        self,
        namespace: CacheNamespace,
        keys: list[str],
    ) -> dict[str, JsonValue]:
        return {}

    @override
    async def put(
        self,
        namespace: CacheNamespace,
        key: str,
        value: JsonValue,
        *,
        ttl: timedelta,
    ) -> None:
        return None

    @override
    async def put_many(
        self,
        namespace: CacheNamespace,
        values: Mapping[str, JsonValue],
        *,
        ttl: timedelta,
    ) -> None:
        return None

    @override
    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        return None


class CachePSqlService(CacheService):
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        protector: CacheEntryProtector | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        if protector is None:
            raw_keys = (settings.CACHE_FERNET_KEY or settings.FERNET_KEY).get_secret_value()
            protector = CacheEntryProtector.from_key_string(raw_keys)
        self._protector = protector

    @override
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None:
        physical_keys = self._protector.physical_keys(namespace, key)
        with timed("cache"):
            async with self._sessionmaker() as db:
                rows = [
                    (row[0], row[1])
                    for row in (
                        await db.execute(
                            _cache_get_statement(),
                            {"cache_namespace": namespace, "cache_keys": physical_keys},
                        )
                    ).all()
                ]
        rows_by_key = dict(rows)
        for candidate in physical_keys:
            if candidate not in rows_by_key:
                continue
            observed = rows_by_key[candidate]
            try:
                if _needs_crypto_offload((observed,)):
                    value = await asyncio.to_thread(
                        self._protector.unprotect, namespace, key, observed
                    )
                else:
                    value = self._protector.unprotect(namespace, key, observed)
            except InvalidCacheEntryError:
                continue
            increment("cache_hits")
            return value
        increment("cache_misses")
        return None

    @override
    async def get_many(
        self,
        namespace: CacheNamespace,
        keys: list[str],
    ) -> dict[str, JsonValue]:
        if not keys:
            return {}
        unique_keys = list(dict.fromkeys(keys))
        if len(unique_keys) >= _BULK_CPU_OFFLOAD_THRESHOLD:
            physical_by_logical = await asyncio.to_thread(
                self._physical_keys_by_logical, namespace, unique_keys
            )
        else:
            physical_by_logical = self._physical_keys_by_logical(namespace, unique_keys)
        physical_key_owners: dict[str, tuple[str, int]] = {}
        for logical_key, physical_keys in physical_by_logical.items():
            for rank, candidate in enumerate(physical_keys):
                owner = (logical_key, rank)
                if physical_key_owners.setdefault(candidate, owner) != owner:
                    return {}
        queried_keys = list(physical_key_owners)
        rows_by_key: dict[str, JsonValue] = {}
        with timed("cache"):
            for offset in range(0, len(queried_keys), _MAX_LOOKUP_KEYS_PER_QUERY):
                chunk = queried_keys[offset : offset + _MAX_LOOKUP_KEYS_PER_QUERY]
                statement = _cache_bulk_get_statement(large=len(chunk) >= _UNNEST_LOOKUP_THRESHOLD)
                async with self._sessionmaker() as db:
                    rows = [
                        (row[0], row[1])
                        for row in (
                            await db.execute(
                                statement,
                                {"cache_namespace": namespace, "cache_keys": chunk},
                            )
                        ).all()
                    ]
                rows_by_key.update(rows)
        if len(physical_by_logical) >= _BULK_CPU_OFFLOAD_THRESHOLD or _needs_crypto_offload(
            rows_by_key.values()
        ):
            values = await asyncio.to_thread(
                self._unprotect_rows, namespace, physical_by_logical, rows_by_key
            )
        else:
            values = self._unprotect_rows(namespace, physical_by_logical, rows_by_key)
        increment("cache_hits", len(values))
        increment("cache_misses", len(physical_by_logical) - len(values))
        return values

    @override
    async def put(
        self,
        namespace: CacheNamespace,
        key: str,
        value: JsonValue,
        *,
        ttl: timedelta,
    ) -> None:
        await self.put_many(namespace, {key: value}, ttl=ttl)

    @override
    async def put_many(
        self,
        namespace: CacheNamespace,
        values: Mapping[str, JsonValue],
        *,
        ttl: timedelta,
    ) -> None:
        if not values:
            return
        if ttl <= timedelta(0):
            msg = "cache ttl must be positive"
            raise ValueError(msg)
        if ttl > _MAX_CACHE_TTL:
            msg = "cache ttl must not exceed five minutes"
            raise ValueError(msg)
        if len(values) >= _BULK_CPU_OFFLOAD_THRESHOLD or _needs_write_crypto_offload(values):
            protected_values = await asyncio.to_thread(
                self._protect_values, namespace, dict(values)
            )
        else:
            protected_values = self._protect_values(namespace, values)
        if protected_values is None:
            return
        # Keep a consistent row-lock order across every atomic write chunk.
        protected_values.sort(key=lambda value: value.key)
        expires_at = func.now() + ttl
        async with self._sessionmaker() as db, db.begin():
            for index, chunk in enumerate(
                batched(protected_values, _MAX_WRITE_ROWS_PER_QUERY, strict=False)
            ):
                parameters: dict[str, object] = {"cache_namespace": namespace}
                if len(chunk) >= _ARRAY_WRITE_THRESHOLD:
                    statement = _cache_array_write_statement(cleanup=index == 0)
                    parameters.update(
                        cache_keys=[value.key for value in chunk],
                        cache_envelopes=[value.envelope for value in chunk],
                        cache_ttl=ttl,
                    )
                else:
                    insert = _cache_insert(namespace, chunk, expires_at)
                    statement = insert.on_conflict_do_update(
                        index_elements=[CacheEntry.namespace, CacheEntry.key],
                        set_={
                            "value": insert.excluded.value,
                            "expires_at": insert.excluded.expires_at,
                        },
                    )
                    if index == 0:
                        statement = statement.add_cte(_cache_write_cleanup_statement())
                await db.execute(statement, parameters)

    @override
    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        await self._purge(
            namespace,
            list(self._protector.physical_keys(namespace, key)),
        )

    async def _purge(self, namespace: CacheNamespace, keys: list[str]) -> None:
        if not keys:
            return
        async with self._sessionmaker() as db, db.begin():
            await db.execute(
                delete(CacheEntry).where(
                    CacheEntry.namespace == namespace,
                    CacheEntry.key.in_(keys),
                )
            )

    def _physical_keys_by_logical(
        self, namespace: CacheNamespace, keys: list[str]
    ) -> dict[str, tuple[str, ...]]:
        return self._protector.physical_keys_many(namespace, keys, include_rotation=True)

    def _unprotect_rows(
        self,
        namespace: CacheNamespace,
        physical_by_logical: dict[str, tuple[str, ...]],
        rows_by_key: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        values: dict[str, JsonValue] = {}
        for logical_key, physical_keys in physical_by_logical.items():
            for candidate in physical_keys:
                if candidate not in rows_by_key:
                    continue
                try:
                    values[logical_key] = self._protector.unprotect(
                        namespace, logical_key, rows_by_key[candidate]
                    )
                except InvalidCacheEntryError:
                    continue
                break
        return values

    def _protect_values(
        self, namespace: CacheNamespace, values: Mapping[str, JsonValue]
    ) -> list[_ProtectedCacheValue] | None:
        physical_key_owners: dict[str, str] = {}
        physical_by_logical = self._protector.physical_keys_many(
            namespace, list(values), include_rotation=False
        )
        for logical_key, candidates in physical_by_logical.items():
            physical_key = candidates[0]
            if physical_key_owners.setdefault(physical_key, logical_key) != logical_key:
                return None
        return [
            _ProtectedCacheValue(
                physical_by_logical[logical_key][0],
                self._protector.protect(namespace, logical_key, value),
            )
            for logical_key, value in values.items()
        ]
