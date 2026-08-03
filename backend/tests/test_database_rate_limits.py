from __future__ import annotations

from datetime import timedelta
from typing import cast
import uuid

from fastapi import Request
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import database_rate_limit_error_handler
from app.rate_limits.service import (
    DatabaseRateLimitExceededError,
    PostgresRateLimitService,
    RateLimitOperation,
    RateLimitPolicy,
)


class _Result:
    def __init__(self, value: int | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> int | None:
        return self._value


class _Session:
    def __init__(self, accepted_count: int | None) -> None:
        self.accepted_count = accepted_count
        self.commits = 0
        self.rollbacks = 0
        self.statement: object | None = None

    async def execute(self, statement: object) -> _Result:
        self.statement = statement
        return _Result(self.accepted_count)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _policy() -> RateLimitPolicy:
    return RateLimitPolicy(
        operation=RateLimitOperation.SMART_PROPOSAL,
        limit=10,
        window=timedelta(minutes=1),
    )


async def test_rate_limit_increment_is_atomic_and_committed() -> None:
    session = _Session(accepted_count=1)
    service = PostgresRateLimitService(db=cast("AsyncSession", session))

    await service.consume(uuid.uuid4(), _policy())

    assert session.commits == 1
    assert session.statement is not None
    sql = str(session.statement)
    assert "ON CONFLICT" in sql
    assert "rate_limit_buckets.count <" in sql
    assert "RETURNING rate_limit_buckets.count" in sql


async def test_rate_limit_rejection_rolls_back_and_returns_retry_after() -> None:
    session = _Session(accepted_count=None)
    service = PostgresRateLimitService(db=cast("AsyncSession", session))

    with pytest.raises(DatabaseRateLimitExceededError) as caught:
        await service.consume(uuid.uuid4(), _policy())

    assert 1 <= caught.value.retry_after <= 60
    assert session.rollbacks == 1
    assert session.commits == 0


@pytest.mark.parametrize("limit", [0, -1])
def test_rate_limit_policy_rejects_nonpositive_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        RateLimitPolicy(
            operation=RateLimitOperation.SMART_CREATE,
            limit=limit,
            window=timedelta(minutes=1),
        )


async def test_rate_limit_http_response_includes_retry_after() -> None:
    response = await database_rate_limit_error_handler(
        cast("Request", None),
        DatabaseRateLimitExceededError(retry_after=17),
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "17"
