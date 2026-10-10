from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from itertools import batched
from typing import override

from pydantic import JsonValue
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
_BULK_CPU_OFFLOAD_THRESHOLD = 256
_MAX_CACHE_TTL = timedelta(minutes=5)


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
        statement = select(CacheEntry.key, CacheEntry.value).where(
            CacheEntry.namespace == namespace,
            CacheEntry.key.in_(physical_keys),
            CacheEntry.expires_at > func.now(),
        )
        with timed("cache"):
            async with self._sessionmaker() as db:
                rows = [(row[0], row[1]) for row in (await db.execute(statement)).all()]
        rows_by_key = dict(rows)
        for candidate in physical_keys:
            if candidate not in rows_by_key:
                continue
            observed = rows_by_key[candidate]
            try:
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
        rows_by_key: dict[str, object] = {}
        with timed("cache"):
            for offset in range(0, len(queried_keys), _MAX_LOOKUP_KEYS_PER_QUERY):
                chunk = queried_keys[offset : offset + _MAX_LOOKUP_KEYS_PER_QUERY]
                statement = select(CacheEntry.key, CacheEntry.value).where(
                    CacheEntry.namespace == namespace,
                    CacheEntry.key.in_(chunk),
                    CacheEntry.expires_at > func.now(),
                )
                async with self._sessionmaker() as db:
                    rows = [(row[0], row[1]) for row in (await db.execute(statement)).all()]
                rows_by_key.update(rows)
        if len(physical_by_logical) >= _BULK_CPU_OFFLOAD_THRESHOLD:
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
        if len(values) >= _BULK_CPU_OFFLOAD_THRESHOLD:
            protected_values = await asyncio.to_thread(
                self._protect_values, namespace, dict(values)
            )
        else:
            protected_values = self._protect_values(namespace, values)
        if protected_values is None:
            return
        expires_at = func.now() + ttl
        for row in protected_values:
            row["expires_at"] = expires_at
        expired = (
            delete(CacheEntry)
            .where(
                or_(
                    CacheEntry.expires_at <= func.now(),
                    (CacheEntry.namespace == namespace) & CacheEntry.key.op("!~")(r"^h[0-9]+\."),
                )
            )
            .cte("delete_expired_cache_entries")
        )
        async with self._sessionmaker() as db, db.begin():
            for index, chunk in enumerate(
                batched(protected_values, _MAX_WRITE_ROWS_PER_QUERY, strict=False)
            ):
                insert = pg_insert(CacheEntry).values(list(chunk))
                statement = insert.on_conflict_do_update(
                    index_elements=[CacheEntry.namespace, CacheEntry.key],
                    set_={
                        "value": insert.excluded.value,
                        "expires_at": insert.excluded.expires_at,
                    },
                )
                if index == 0:
                    statement = statement.add_cte(expired)
                await db.execute(statement)

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
        return {key: self._protector.physical_keys(namespace, key) for key in keys}

    def _unprotect_rows(
        self,
        namespace: CacheNamespace,
        physical_by_logical: dict[str, tuple[str, ...]],
        rows_by_key: dict[str, object],
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
    ) -> list[dict[str, object]] | None:
        physical_key_owners: dict[str, str] = {}
        active_physical_keys: dict[str, str] = {}
        for logical_key in values:
            physical_key = self._protector.physical_keys(namespace, logical_key)[0]
            if physical_key_owners.setdefault(physical_key, logical_key) != logical_key:
                return None
            active_physical_keys[logical_key] = physical_key
        return [
            {
                "namespace": namespace,
                "key": active_physical_keys[logical_key],
                "value": self._protector.protect(namespace, logical_key, value),
            }
            for logical_key, value in values.items()
        ]
