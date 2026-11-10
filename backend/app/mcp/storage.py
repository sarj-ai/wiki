import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.mcp.protection import OAuthStateProtector


_MAX_IDENTITY_BYTES = 8192
_OPERATION_TIMEOUT = timedelta(seconds=30)


@dataclass(frozen=True)
class _Operation:
    session: AsyncSession
    task: asyncio.Task[object] | None
    identity: str


class PostgresOAuthOperations:
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
        if not identity or len(identity.encode()) > _MAX_IDENTITY_BYTES:
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
