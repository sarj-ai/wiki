from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.performance import (
    begin_request_metrics,
    provider_retry_budget,
    provider_retry_remaining_seconds,
    reset_request_metrics,
)


class _SentinelError(Exception):
    pass


async def _probe_worker_budget_then_fail(seen: list[float | None]) -> None:
    with provider_retry_budget(timedelta(minutes=4)):
        seen.append(await asyncio.to_thread(provider_retry_remaining_seconds))
        raise _SentinelError


def _enter_invalid_budget() -> None:
    with provider_retry_budget(timedelta(milliseconds=999)):
        pytest.fail("invalid budget must not enter its context")


async def test_provider_retry_budget_propagates_to_thread_and_restores_after_error() -> None:
    tokens = begin_request_metrics()
    seen: list[float | None] = []
    try:
        outer_budget = provider_retry_remaining_seconds()
        with pytest.raises(_SentinelError):
            await _probe_worker_budget_then_fail(seen)
        restored_budget = provider_retry_remaining_seconds()
    finally:
        reset_request_metrics(tokens)

    assert outer_budget is not None
    [worker_budget] = seen
    assert worker_budget is not None
    assert worker_budget > 230
    assert restored_budget is not None
    assert restored_budget <= outer_budget


def test_provider_retry_budget_rejects_less_than_one_attempt() -> None:
    with pytest.raises(ValueError, match="at least one request attempt"):
        _enter_invalid_budget()
