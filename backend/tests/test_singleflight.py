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

    with pytest.raises(RuntimeError, match="boom"):
        await task_a
    with pytest.raises(RuntimeError, match="boom"):
        await task_b


async def test_cancelled_follower_does_not_cancel_leader_or_other_followers() -> None:
    sf = Singleflight[str]()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def work() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "done"

    leader = asyncio.create_task(sf.do("shared", work))
    await started.wait()
    cancelled = asyncio.create_task(sf.do("shared", work))
    survivor = asyncio.create_task(sf.do("shared", work))
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()
    assert await asyncio.gather(leader, survivor) == ["done", "done"]
    assert calls == 1
    assert await sf.do("shared", work) == "done"


async def test_cancelled_leader_releases_waiters_and_allows_a_fresh_retry() -> None:
    sf = Singleflight[str]()
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> str:
        started.set()
        await release.wait()
        return "done"

    leader = asyncio.create_task(sf.do("shared", work))
    await started.wait()
    follower = asyncio.create_task(sf.do("shared", work))
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    with pytest.raises(asyncio.CancelledError):
        await follower
    release.set()
    assert await sf.do("shared", work) == "done"


@pytest.mark.parametrize(
    "value",
    [None, RuntimeError("value"), BaseException("value"), asyncio.CancelledError("value")],
    ids=["none", "runtime-error-value", "base-exception-value", "cancelled-error-value"],
)
async def test_exception_valued_success_is_returned_to_followers(
    value: BaseException | None,
) -> None:
    sf = Singleflight[BaseException | None]()
    started = asyncio.Event()
    release = asyncio.Event()

    async def work() -> BaseException | None:
        started.set()
        await release.wait()
        return value

    leader = asyncio.create_task(sf.do("shared", work))
    await started.wait()
    follower = asyncio.create_task(sf.do("shared", work))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(leader, follower)

    assert all(result is value for result in results)
    assert all(not task.cancelled() and task.exception() is None for task in (leader, follower))
    assert await sf.do("shared", work) is value


@pytest.mark.parametrize("error_type", [RuntimeError, BaseException])
async def test_coalesced_failure_preserves_exception_identity(
    error_type: type[BaseException],
) -> None:
    sf = Singleflight[str]()
    started = asyncio.Event()
    release = asyncio.Event()
    failure = error_type("provider-failure")

    async def work() -> str:
        started.set()
        await release.wait()
        raise failure

    leader = asyncio.create_task(sf.do("shared", work))
    await started.wait()
    follower = asyncio.create_task(sf.do("shared", work))
    await asyncio.sleep(0)
    release.set()

    for task in (leader, follower):
        with pytest.raises(error_type) as caught:
            await task
        assert caught.value is failure

    async def retry() -> str:
        return "fresh"

    assert await sf.do("shared", retry) == "fresh"
