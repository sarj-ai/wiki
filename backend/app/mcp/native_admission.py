import asyncio
from datetime import datetime, timedelta
import math

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.mcp_registration_admission import McpRegistrationAdmission


# Fixed namespace, not an encryption-key-derived lock: all replicas/rotations agree.
_ADMISSION_LOCK = 0x57494B4944435231
_ADMISSION_TIMEOUT = timedelta(seconds=3)
_WINDOWS = ((10, timedelta(seconds=60)), (300, timedelta(seconds=1800)))
_SINGLETON = 1
_MAX_RETRY = 1800


class RegistrationAdmissionDeniedError(Exception):
    def __init__(self, retry_after: timedelta) -> None:
        super().__init__("Wiki registration admission exhausted")
        self.retry_after_seconds = max(1, min(_MAX_RETRY, math.ceil(retry_after.total_seconds())))


class RegistrationAdmissionUnavailableError(Exception):
    def __init__(self) -> None:
        super().__init__("Wiki registration admission unavailable")


async def _database_time(db: AsyncSession) -> datetime:
    now = await db.scalar(select(func.clock_timestamp()))
    if not isinstance(now, datetime):
        raise RegistrationAdmissionUnavailableError
    return now


class PostgresRegistrationAdmission:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def admit(self) -> None:
        # Own the commit before registration; later registration failures never refund it.
        try:
            await self._admit()
        except SQLAlchemyError, TimeoutError:
            raise RegistrationAdmissionUnavailableError from None

    async def _admit(self) -> None:
        async with (
            asyncio.timeout(_ADMISSION_TIMEOUT.total_seconds()),
            self._sessions.begin() as db,
        ):
            await db.execute(text("SET LOCAL lock_timeout = '1s'"))
            await db.execute(text("SET LOCAL statement_timeout = '1s'"))
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:identity)"),
                {"identity": _ADMISSION_LOCK},
            )
            now = await _database_time(db)
            row = await db.get(McpRegistrationAdmission, _SINGLETON)
            history = sorted(
                value for value in (row.admitted_at if row else []) if value > now - _WINDOWS[-1][1]
            )
            retry = 0
            for limit, window in _WINDOWS:
                active = [value for value in history if value > now - window]
                if len(active) >= limit:
                    retry = max(
                        retry,
                        math.ceil((active[len(active) - limit] + window - now).total_seconds()),
                    )
            if retry:
                raise RegistrationAdmissionDeniedError(timedelta(seconds=retry))
            history.append(now)
            if row is None:
                db.add(McpRegistrationAdmission(id=_SINGLETON, admitted_at=history))
            else:
                row.admitted_at = history
