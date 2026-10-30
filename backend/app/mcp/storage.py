# Provider operations must acquire operation() before revalidating codes/state.
# Every refresh and revoke must use the same grant identity. An external IdP
# refresh and a database commit are not an atomic distributed operation.

import asyncio
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
import math
from typing import NamedTuple, SupportsFloat, override

from key_value.aio.protocols import AsyncKeyValue
from pydantic import JsonValue
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.mcp.protection import InvalidOAuthStateError, OAuthStateProtector
from app.models.mcp_oauth import MCPOAuthEntry


COLLECTIONS = frozenset(
    {
        "mcp-upstream-tokens",
        "mcp-oauth-proxy-clients",
        "mcp-oauth-transactions",
        "mcp-authorization-codes",
        "mcp-jti-mappings",
        "mcp-refresh-tokens",
    }
)
_MAX_KEY_BYTES = 8192
_MAX_BULK = 100
_MAX_TTL = timedelta(days=366)
_OPERATION_TIMEOUT = timedelta(seconds=30)


class OAuthStoredValue(NamedTuple):
    value: dict[str, JsonValue] | None
    remaining: float | None


@dataclass(frozen=True)
class _Operation:
    session: AsyncSession
    task: asyncio.Task[object] | None
    identity: str


def _validate_key(collection: str | None, key: object) -> str:
    if collection not in COLLECTIONS:
        msg = "Unsupported OAuth storage collection"
        raise ValueError(msg)
    if not isinstance(key, str) or not key or len(key.encode()) > _MAX_KEY_BYTES:
        msg = "Invalid OAuth storage key"
        raise ValueError(msg)
    return str(collection)


def _ttl_seconds(ttl: SupportsFloat | None) -> float | None:
    if ttl is None:
        return None
    seconds = float(ttl)
    if (
        isinstance(ttl, bool)
        or not math.isfinite(seconds)
        or not 0 < seconds <= _MAX_TTL.total_seconds()
    ):
        msg = "OAuth TTL must be positive, finite, and at most 366 days"
        raise ValueError(msg)
    return seconds


class PostgresOAuthStorage(AsyncKeyValue):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        protector: OAuthStateProtector,
    ) -> None:
        self._sessions = sessions
        self._protector = protector
        self._operation: ContextVar[_Operation | None] = ContextVar("mcp_oauth_operation")

    def _current(self) -> _Operation | None:
        operation = self._operation.get(None)
        if operation is not None and operation.task is not asyncio.current_task():
            msg = "OAuth transaction cannot be shared with child tasks"
            raise RuntimeError(msg)
        return operation

    @asynccontextmanager
    async def operation(self, identity: str) -> AsyncGenerator[AsyncSession]:
        # Lock identity remains stable across key rotation; revalidate after entry.
        # This context owns commit/rollback. Callers may join grant writes through
        # the yielded session, but must not commit, rollback, close, retain, or
        # share it with another task. Same-identity nesting joins, not a savepoint.
        if not identity or len(identity.encode()) > _MAX_KEY_BYTES:
            msg = "Invalid OAuth operation identity"
            raise ValueError(msg)
        current = self._current()
        if current is not None:
            if current.identity != identity:
                msg = "Nested OAuth operations must use the same identity"
                raise RuntimeError(msg)
            yield current.session
            return
        async with (
            asyncio.timeout(_OPERATION_TIMEOUT.total_seconds()),
            self._sessions.begin() as session,
        ):
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(text("SET LOCAL statement_timeout = '10s'"))
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": self._protector.lock_id(identity)},
            )
            token = self._operation.set(_Operation(session, asyncio.current_task(), identity))
            try:
                yield session
            finally:
                self._operation.reset(token)

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession]:
        current = self._current()
        if current is not None:
            yield current.session
        else:
            async with (
                asyncio.timeout(_OPERATION_TIMEOUT.total_seconds()),
                self._sessions.begin() as session,
            ):
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                yield session

    @override
    async def ttl(
        self,
        key: str,
        *,
        collection: str | None = None,
    ) -> OAuthStoredValue:
        namespace = _validate_key(collection, key)
        digests = self._protector.physical_keys(namespace, key)
        async with self._session() as session:
            result = await session.execute(
                select(
                    MCPOAuthEntry.ciphertext,
                    MCPOAuthEntry.expires_at,
                    func.clock_timestamp(),
                ).where(
                    MCPOAuthEntry.collection == namespace,
                    MCPOAuthEntry.key_digest.in_(digests),
                )
            )
            rows = result.all()
            # Multiple physical aliases may represent stale rolling writers. Never
            # pick a winner silently: deployment must coordinate key-ring changes.
            if len(rows) > 1:
                raise InvalidOAuthStateError
            if not rows:
                return OAuthStoredValue(None, None)
            ciphertext, expires_at, now = rows[0]
            remaining = None if expires_at is None else (expires_at - now).total_seconds()
            if remaining is not None and remaining <= 0:
                return OAuthStoredValue(None, None)
            return OAuthStoredValue(
                self._protector.unprotect(namespace, key, ciphertext), remaining
            )

    @override
    async def get(self, key: str, *, collection: str | None = None) -> dict[str, JsonValue] | None:
        value, _ = await self.ttl(key, collection=collection)
        return value

    @override
    async def put(
        self,
        key: str,
        value: Mapping[str, object],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        namespace = _validate_key(collection, key)
        seconds = _ttl_seconds(ttl)
        ciphertext = self._protector.protect(namespace, key, value)
        digests = self._protector.physical_keys(namespace, key)
        async with self._session() as session:
            # The stable physical-key lock also serializes overlapping key rings.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": self._protector.lock_id(f"entry:{namespace}:{key}")},
            )
            await session.execute(
                delete(MCPOAuthEntry).where(
                    MCPOAuthEntry.collection == namespace,
                    MCPOAuthEntry.key_digest.in_(digests[1:]),
                )
            )
            expires = (
                None if seconds is None else func.clock_timestamp() + timedelta(seconds=seconds)
            )
            statement = insert(MCPOAuthEntry).values(
                collection=namespace,
                key_digest=digests[0],
                ciphertext=ciphertext,
                expires_at=expires,
                updated_at=func.clock_timestamp(),
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[MCPOAuthEntry.collection, MCPOAuthEntry.key_digest],
                    set_={
                        "ciphertext": statement.excluded.ciphertext,
                        "expires_at": statement.excluded.expires_at,
                        "updated_at": statement.excluded.updated_at,
                    },
                )
            )

    @override
    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        namespace = _validate_key(collection, key)
        async with self._session() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": self._protector.lock_id(f"entry:{namespace}:{key}")},
            )
            result = await session.execute(
                delete(MCPOAuthEntry)
                .where(
                    MCPOAuthEntry.collection == namespace,
                    MCPOAuthEntry.key_digest.in_(self._protector.physical_keys(namespace, key)),
                )
                .returning(MCPOAuthEntry.key_digest)
            )
            return bool(result.all())

    async def cleanup_expired(self, *, limit: int = 100) -> int:
        # Bounded index-backed cleanup is separate from the write path.
        if type(limit) is not int or not 1 <= limit <= _MAX_BULK:
            msg = "OAuth cleanup limit must be between one and 100"
            raise ValueError(msg)
        async with self._session() as session:
            expired = (
                select(MCPOAuthEntry.collection, MCPOAuthEntry.key_digest)
                .where(MCPOAuthEntry.expires_at <= func.clock_timestamp())
                .order_by(MCPOAuthEntry.expires_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .cte("expired_oauth_entries")
            )
            result = await session.execute(
                delete(MCPOAuthEntry)
                .where(
                    MCPOAuthEntry.collection == expired.c.collection,
                    MCPOAuthEntry.key_digest == expired.c.key_digest,
                )
                .returning(MCPOAuthEntry.key_digest)
            )
            return len(result.all())

    @staticmethod
    def _validate_bulk(keys: Sequence[str], collection: str | None) -> None:
        if isinstance(keys, (str, bytes)) or len(keys) > _MAX_BULK:
            msg = "OAuth bulk request exceeds limit"
            raise ValueError(msg)
        if collection not in COLLECTIONS:
            msg = "Unsupported OAuth storage collection"
            raise ValueError(msg)
        for key in keys:
            _validate_key(collection, key)

    @override
    async def get_many(
        self,
        keys: Sequence[str],
        *,
        collection: str | None = None,
    ) -> list[dict[str, JsonValue] | None]:
        self._validate_bulk(keys, collection)
        return [await self.get(key, collection=collection) for key in keys]

    @override
    async def ttl_many(
        self,
        keys: Sequence[str],
        *,
        collection: str | None = None,
    ) -> list[tuple[dict[str, JsonValue] | None, float | None]]:
        self._validate_bulk(keys, collection)
        return [await self.ttl(key, collection=collection) for key in keys]

    @override
    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, object]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        self._validate_bulk(keys, collection)
        _ttl_seconds(ttl)
        if len(keys) != len(values) or len(set(keys)) != len(keys):
            msg = "OAuth bulk keys and values must match without duplicate keys"
            raise ValueError(msg)
        # Validate every payload before any write; SDK does not promise atomic bulk.
        for key, value in zip(keys, values, strict=True):
            self._protector.protect(str(collection), key, value)
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)

    @override
    async def delete_many(self, keys: Sequence[str], *, collection: str | None = None) -> int:
        self._validate_bulk(keys, collection)
        return sum([await self.delete(key, collection=collection) for key in dict.fromkeys(keys)])
