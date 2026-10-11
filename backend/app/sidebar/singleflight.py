from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class Singleflight[T]:
    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future[T]] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None:
            # A cancelled follower must not cancel the shared Future and break
            # the leader or other followers.
            return await asyncio.shield(existing)

        # No suspension occurs between lookup and insertion. This state belongs
        # to one event loop, so only the leader needs a Future or dictionary entry.
        fut: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            result = await fn()
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()
            raise
        else:
            fut.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)
