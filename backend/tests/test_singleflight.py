from __future__ import annotations

import asyncio

import pytest

from app.sidebar.singleflight import Singleflight


async def test_coalesces_concurrent_calls_onto_one_fn() -> None:
    sf = Singleflight()
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "done"

    task_a = asyncio.create_task(sf.do("k", work))
    await started.wait()
    task_b = asyncio.create_task(sf.do("k", work))
    task_c = asyncio.create_task(sf.do("k", work))
    await asyncio.sleep(0)
    release.set()
    results = list(await asyncio.gather(task_a, task_b, task_c))

    assert results == ["done", "done", "done"]
    assert calls == 1


async def test_different_keys_are_independent() -> None:
    sf = Singleflight()
    calls: dict[str, int] = {"a": 0, "b": 0}

    async def work(key: str) -> str:
        calls[key] += 1
        return key.upper()

    a = await sf.do("a", lambda: work("a"))
    b = await sf.do("b", lambda: work("b"))

    assert a == "A"
    assert b == "B"
    assert calls == {"a": 1, "b": 1}


async def test_entry_cleared_after_completion() -> None:
    sf = Singleflight()
    calls = 0

    async def work() -> int:
        nonlocal calls
        calls += 1
        return calls

    first = await sf.do("k", work)
    second = await sf.do("k", work)

    assert first == 1
    assert second == 2


async def test_exception_propagates_and_clears_entry() -> None:
    sf = Singleflight()

    async def boom() -> str:
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="boom"):
        await sf.do("k", boom)

    async def ok() -> str:
        return "ok"

    assert await sf.do("k", ok) == "ok"


async def test_exception_propagates_to_concurrent_waiters() -> None:
    sf = Singleflight()
    started = asyncio.Event()
    release = asyncio.Event()

    async def boom() -> str:
        started.set()
        await release.wait()
        msg = "boom"
        raise RuntimeError(msg)

    task_a = asyncio.create_task(sf.do("k", boom))
    await started.wait()
    task_b = asyncio.create_task(sf.do("k", boom))
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(RuntimeError):
        await task_a
    with pytest.raises(RuntimeError):
        await task_b
