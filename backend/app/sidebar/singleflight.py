from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _Flight[T]:
    future: asyncio.Future[T] | None = None


class Singleflight[T]:
    def __init__(self) -> None:
        self._inflight: dict[str, _Flight[T]] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            if existing.future is None:
                existing.future = asyncio.get_running_loop().create_future()
            # A cancelled follower must not cancel the shared Future and break
            # the leader or other followers.
            return await asyncio.shield(existing.future)

        # No suspension occurs between lookup and insertion. This state belongs
        # to one event loop. A Future is needed only when a follower arrives.
        flight: _Flight[T] = _Flight()
        self._inflight[key] = flight
        try:
            result = await fn()
        except BaseException as exc:
            if flight.future is not None:
                flight.future.set_exception(exc)
                flight.future.exception()
            raise
        else:
            if flight.future is not None:
                flight.future.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)
