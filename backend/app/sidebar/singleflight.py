"""In-memory singleflight for coalescing duplicate async work.

See PIVOT_PLAN.md §7.3. When multiple requests ask for the same
drive walk or folder listing concurrently, we want exactly one real
Drive API call — every concurrent caller awaits the same future.

Scope is per-process. A single Cloud Run worker with a single async
event loop gives perfect coalescing at current Sarj scale. §7.4
describes the Postgres advisory-lock upgrade for multi-worker scale.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

T = TypeVar("T")


class Singleflight:
    """Coalesce concurrent calls with the same key onto one future.

    Classic singleflight pattern: the first caller for a given key
    runs the work, every concurrent caller awaits the same future,
    and the entry is removed once the work finishes so the next call
    rebuilds fresh state.

    Return type is generic on the method so a single instance can
    serve heterogeneous operations keyed by string.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` once per key across concurrent callers."""
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is None:
                loop = asyncio.get_running_loop()
                fut: asyncio.Future[T] = loop.create_future()
                self._inflight[key] = fut
            else:
                fut = None  # type: ignore[assignment]

        # Release the lock BEFORE awaiting the existing future. Awaiting
        # under the lock would serialize every waiter through it.
        if existing is not None:
            return cast("T", await existing)

        try:
            result = await fn()
        except BaseException as exc:
            fut.set_exception(exc)
            async with self._lock:
                self._inflight.pop(key, None)
            raise
        fut.set_result(result)
        async with self._lock:
            self._inflight.pop(key, None)
        return result
