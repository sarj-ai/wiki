from __future__ import annotations

import asyncio
import time

import pytest

from app.drive import drives_cache
from app.drive.schemas import DriveSummary


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    drives_cache.clear()


def _drives(*names: str) -> list[DriveSummary]:
    return [DriveSummary(id=f"d-{n}", name=n) for n in names] or [
        DriveSummary(id="d1", name="One"),
    ]


class _Fetcher:
    def __init__(self, returns: list[list[DriveSummary]]) -> None:
        self._returns = returns
        self.calls = 0

    async def __call__(self) -> list[DriveSummary]:
        idx = min(self.calls, len(self._returns) - 1)
        self.calls += 1
        return self._returns[idx]


@pytest.mark.asyncio
async def test_cold_cache_calls_fetcher_synchronously() -> None:
    fetcher = _Fetcher([_drives("Alpha")])
    drives = await drives_cache.get_drives("alice", fetcher)
    assert fetcher.calls == 1
    assert [d.name for d in drives] == ["Alpha"]


@pytest.mark.asyncio
async def test_fresh_cache_skips_fetcher() -> None:
    fetcher = _Fetcher([_drives("Alpha")])
    await drives_cache.get_drives("alice", fetcher)
    drives = await drives_cache.get_drives("alice", fetcher)
    assert fetcher.calls == 1
    assert [d.name for d in drives] == ["Alpha"]


@pytest.mark.asyncio
async def test_stale_cache_returns_cached_and_refreshes_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(time, "monotonic", lambda: now)

    fetcher = _Fetcher([_drives("Alpha"), _drives("Beta")])
    await drives_cache.get_drives("alice", fetcher)
    assert fetcher.calls == 1

    now = base + drives_cache._FRESH_SECONDS + 1
    drives = await drives_cache.get_drives("alice", fetcher)
    assert [d.name for d in drives] == ["Alpha"]

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fetcher.calls == 2

    drives = await drives_cache.get_drives("alice", fetcher)
    assert fetcher.calls == 2
    assert [d.name for d in drives] == ["Beta"]


@pytest.mark.asyncio
async def test_concurrent_stale_reads_singleflight(monkeypatch: pytest.MonkeyPatch) -> None:
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(time, "monotonic", lambda: now)

    fetcher = _Fetcher([_drives("Alpha"), _drives("Beta")])
    await drives_cache.get_drives("alice", fetcher)

    now = base + drives_cache._FRESH_SECONDS + 1
    a, b = await asyncio.gather(
        drives_cache.get_drives("alice", fetcher),
        drives_cache.get_drives("alice", fetcher),
    )
    assert [d.name for d in a] == ["Alpha"]
    assert [d.name for d in b] == ["Alpha"]

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_hard_expiry_falls_back_to_cold_path(monkeypatch: pytest.MonkeyPatch) -> None:
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(time, "monotonic", lambda: now)

    fetcher = _Fetcher([_drives("Alpha"), _drives("Beta")])
    await drives_cache.get_drives("alice", fetcher)

    now = base + drives_cache._HARD_EXPIRY_SECONDS + 1
    drives = await drives_cache.get_drives("alice", fetcher)
    assert [d.name for d in drives] == ["Beta"]
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_invalidate_drops_only_targeted_user() -> None:
    fetcher_a = _Fetcher([_drives("AliceDrive")])
    fetcher_b = _Fetcher([_drives("BobDrive")])
    await drives_cache.get_drives("alice", fetcher_a)
    await drives_cache.get_drives("bob", fetcher_b)

    drives_cache.invalidate("alice")

    await drives_cache.get_drives("alice", fetcher_a)
    assert fetcher_a.calls == 2
    await drives_cache.get_drives("bob", fetcher_b)
    assert fetcher_b.calls == 1


@pytest.mark.asyncio
async def test_invalidate_unknown_user_is_a_noop() -> None:
    fetcher = _Fetcher([_drives("Alpha")])
    await drives_cache.get_drives("alice", fetcher)

    drives_cache.invalidate("ghost")

    drives = await drives_cache.get_drives("alice", fetcher)
    assert fetcher.calls == 1
    assert [d.name for d in drives] == ["Alpha"]


@pytest.mark.asyncio
async def test_clear_drops_every_entry_and_marker() -> None:
    fetcher = _Fetcher([_drives("Alpha")])
    await drives_cache.get_drives("alice", fetcher)
    await drives_cache.get_drives("bob", fetcher)
    drives_cache.clear()

    fetcher2 = _Fetcher([_drives("Beta")])
    drives = await drives_cache.get_drives("alice", fetcher2)
    assert fetcher2.calls == 1
    assert [d.name for d in drives] == ["Beta"]


@pytest.mark.asyncio
async def test_background_refresh_failure_keeps_serving_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(time, "monotonic", lambda: now)

    call_count = 0

    async def flaky_fetcher() -> list[DriveSummary]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _drives("Alpha")
        if call_count == 2:
            msg = "Drive 502"
            raise RuntimeError(msg)
        return _drives("Beta")

    await drives_cache.get_drives("alice", flaky_fetcher)
    now = base + drives_cache._FRESH_SECONDS + 1
    drives = await drives_cache.get_drives("alice", flaky_fetcher)
    assert [d.name for d in drives] == ["Alpha"]

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    drives = await drives_cache.get_drives("alice", flaky_fetcher)
    assert [d.name for d in drives] == ["Alpha"]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    drives = await drives_cache.get_drives("alice", flaky_fetcher)
    assert [d.name for d in drives] == ["Beta"]
