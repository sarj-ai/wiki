from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import override
import uuid

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import RateLimitBucket


class RateLimitOperation(StrEnum):
    SMART_PROPOSAL = "file.smart_proposal"
    SMART_CREATE = "file.smart_create"


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    operation: RateLimitOperation
    limit: int
    window: timedelta

    def __post_init__(self) -> None:
        if self.limit <= 0:
            msg = "limit must be positive"
            raise ValueError(msg)
        if self.window <= timedelta(0):
            msg = "window must be positive"
            raise ValueError(msg)


class DatabaseRateLimitExceededError(Exception):
    def __init__(self, *, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("Rate limit exceeded")


class RateLimitService(ABC):
    @abstractmethod
    async def consume(self, principal_id: uuid.UUID, policy: RateLimitPolicy) -> None: ...


class PostgresRateLimitService(RateLimitService):
    def __init__(self, *, db: AsyncSession) -> None:
        self._db = db

    @override
    async def consume(self, principal_id: uuid.UUID, policy: RateLimitPolicy) -> None:
        window_seconds = policy.window.total_seconds()
        window_start = func.to_timestamp(
            func.floor(func.extract("epoch", func.now()) / window_seconds) * window_seconds
        )
        statement = (
            pg_insert(RateLimitBucket)
            .values(
                principal_id=principal_id,
                operation=policy.operation,
                window_start=window_start,
                count=1,
                expires_at=window_start + policy.window,
            )
            .on_conflict_do_update(
                index_elements=[
                    RateLimitBucket.principal_id,
                    RateLimitBucket.operation,
                    RateLimitBucket.window_start,
                ],
                set_={
                    "count": RateLimitBucket.count + 1,
                    "updated_at": func.now(),
                },
                where=RateLimitBucket.count < policy.limit,
            )
            .returning(RateLimitBucket.count)
        )
        accepted_count = (await self._db.execute(statement)).scalar_one_or_none()
        if accepted_count is None:
            await self._db.rollback()
            raise DatabaseRateLimitExceededError(
                retry_after=max(1, round(policy.window.total_seconds()))
            )
        await self._db.commit()
