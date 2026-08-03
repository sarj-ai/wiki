from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import cast, override

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.wiki import CacheEntry


_JSON_VALUE: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class CacheNamespace(StrEnum):
    DRIVE_LIST = "drive.list"
    SHORTCUT_PROBE = "drive.shortcut_probe"
    TEMPLATE_CATALOG = "template.catalog"


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


class CachePSqlService(CacheService):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @override
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None:
        statement = select(CacheEntry.value).where(
            CacheEntry.namespace == namespace,
            CacheEntry.key == key,
            CacheEntry.expires_at > func.now(),
        )
        async with self._sessionmaker() as db:
            value = (await db.execute(statement)).scalar_one_or_none()
        return None if value is None else _JSON_VALUE.validate_python(value)

    @override
    async def get_many(
        self,
        namespace: CacheNamespace,
        keys: list[str],
    ) -> dict[str, JsonValue]:
        if not keys:
            return {}
        statement = select(CacheEntry.key, CacheEntry.value).where(
            CacheEntry.namespace == namespace,
            CacheEntry.key.in_(keys),
            CacheEntry.expires_at > func.now(),
        )
        async with self._sessionmaker() as db:
            rows = (await db.execute(statement)).all()
        return {
            key: _JSON_VALUE.validate_python(value)
            for key, value in cast("list[tuple[str, object]]", rows)
        }

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
        expired = (
            delete(CacheEntry)
            .where(CacheEntry.expires_at <= func.now())
            .cte("delete_expired_cache_entries")
        )
        insert = pg_insert(CacheEntry).values(
            [
                {
                    "namespace": namespace,
                    "key": key,
                    "value": _JSON_VALUE.validate_python(value),
                    "expires_at": func.now() + ttl,
                }
                for key, value in values.items()
            ]
        )
        statement = insert.on_conflict_do_update(
            index_elements=[CacheEntry.namespace, CacheEntry.key],
            set_={
                "value": insert.excluded.value,
                "expires_at": insert.excluded.expires_at,
            },
        ).add_cte(expired)
        async with self._sessionmaker() as db, db.begin():
            await db.execute(statement)

    @override
    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        async with self._sessionmaker() as db, db.begin():
            await db.execute(
                delete(CacheEntry).where(
                    CacheEntry.namespace == namespace,
                    CacheEntry.key == key,
                )
            )
