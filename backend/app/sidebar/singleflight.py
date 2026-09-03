from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class Singleflight[T]:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Future[T]] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[T] = loop.create_future()
        async with self._lock:
            existing = self._inflight.get(key)
            if existing is None:
                self._inflight[key] = fut

        if existing is not None:
            return await existing

        try:
            result = await fn()
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()
            async with self._lock:
                self._inflight.pop(key, None)
            raise
        fut.set_result(result)
        async with self._lock:
            self._inflight.pop(key, None)
        return result
