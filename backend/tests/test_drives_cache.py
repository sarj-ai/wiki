"""Unit tests for the per-user stale-while-revalidate drives cache."""

from __future__ import annotations

import asyncio

import pytest

from app.drive import drives_cache
from app.drive.schemas import DriveSummary


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test starts with an empty cache so module-level state
    doesn't leak across cases."""
    drives_cache.clear()


def _drives(*names: str) -> list[DriveSummary]:
    return [DriveSummary(id=f"d-{n}", name=n) for n in names] or [
        DriveSummary(id="d1", name="One"),
    ]


class _Fetcher:
    """Records every invocation so tests can assert call count without
    mocking. ``returns`` is the value (or sequence) yielded on each
    call; the first invocation gets ``returns[0]``, etc., clamping to
    the last entry when out of values.
    """

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
    assert fetcher.calls == 1  # still only the cold-path call
    assert [d.name for d in drives] == ["Alpha"]


@pytest.mark.asyncio
async def test_stale_cache_returns_cached_and_refreshes_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the entry is older than the freshness window but inside the
    hard-expiry window, the read returns immediately with the cached
    value AND kicks off a background refresh."""
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(drives_cache.time, "monotonic", lambda: now)

    fetcher = _Fetcher([_drives("Alpha"), _drives("Beta")])
    await drives_cache.get_drives("alice", fetcher)
    assert fetcher.calls == 1

    now = base + drives_cache._FRESH_SECONDS + 1  # past freshness, well inside hard expiry
    drives = await drives_cache.get_drives("alice", fetcher)
    # The synchronous call returned the OLD value — fast path.
    assert [d.name for d in drives] == ["Alpha"]

    # Let the background task run. asyncio.create_task is scheduled on
    # the running loop; one event-loop tick is enough for it to land.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fetcher.calls == 2

    # Subsequent read serves the refreshed value without a new fetch.
    drives = await drives_cache.get_drives("alice", fetcher)
    assert fetcher.calls == 2
    assert [d.name for d in drives] == ["Beta"]


@pytest.mark.asyncio
async def test_concurrent_stale_reads_singleflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two concurrent stale reads must both serve cached, but only ONE
    background refresh fires — singleflight gates the revalidation so
    a burst doesn't fan out to N redundant Drive calls."""
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(drives_cache.time, "monotonic", lambda: now)

    fetcher = _Fetcher([_drives("Alpha"), _drives("Beta")])
    await drives_cache.get_drives("alice", fetcher)

    now = base + drives_cache._FRESH_SECONDS + 1
    a, b = await asyncio.gather(
        drives_cache.get_drives("alice", fetcher),
        drives_cache.get_drives("alice", fetcher),
    )
    assert [d.name for d in a] == ["Alpha"]
    assert [d.name for d in b] == ["Alpha"]

    # Drain the background tasks. Two stale reads, but only one refresh.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert fetcher.calls == 2  # 1 cold + 1 background, NOT 1 cold + 2


@pytest.mark.asyncio
async def test_hard_expiry_falls_back_to_cold_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry older than the hard-expiry ceiling is dropped instead
    of being served stale + refreshed. This caps memory growth from
    inactive sessions and worst-case staleness for users that come
    back days later."""
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(drives_cache.time, "monotonic", lambda: now)

    fetcher = _Fetcher([_drives("Alpha"), _drives("Beta")])
    await drives_cache.get_drives("alice", fetcher)

    now = base + drives_cache._HARD_EXPIRY_SECONDS + 1
    drives = await drives_cache.get_drives("alice", fetcher)
    # Hard-expired path is synchronous — must return the FRESH value,
    # not the stale one.
    assert [d.name for d in drives] == ["Beta"]
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_invalidate_drops_only_targeted_user() -> None:
    fetcher_a = _Fetcher([_drives("AliceDrive")])
    fetcher_b = _Fetcher([_drives("BobDrive")])
    await drives_cache.get_drives("alice", fetcher_a)
    await drives_cache.get_drives("bob", fetcher_b)

    drives_cache.invalidate("alice")

    # Alice now goes through the fetcher again.
    await drives_cache.get_drives("alice", fetcher_a)
    assert fetcher_a.calls == 2
    # Bob's cache is untouched.
    await drives_cache.get_drives("bob", fetcher_b)
    assert fetcher_b.calls == 1


@pytest.mark.asyncio
async def test_invalidate_unknown_user_is_a_noop() -> None:
    drives_cache.invalidate("ghost")  # must not raise


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
    """If the background refetch raises, the cached value is left in
    place — a transient Drive blip must not blank the cache mid-session.
    The next read sees the entry as stale-and-not-revalidating, so it
    schedules another attempt."""
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(drives_cache.time, "monotonic", lambda: now)

    call_count = 0

    async def flaky_fetcher() -> list[DriveSummary]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _drives("Alpha")  # cold-path success
        if call_count == 2:
            raise RuntimeError("Drive 502")  # background refresh fails
        return _drives("Beta")  # next refresh succeeds

    await drives_cache.get_drives("alice", flaky_fetcher)
    now = base + drives_cache._FRESH_SECONDS + 1
    drives = await drives_cache.get_drives("alice", flaky_fetcher)
    assert [d.name for d in drives] == ["Alpha"]  # stale served

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # After the failed refresh, the cached entry is still Alpha.
    drives = await drives_cache.get_drives("alice", flaky_fetcher)
    assert [d.name for d in drives] == ["Alpha"]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    drives = await drives_cache.get_drives("alice", flaky_fetcher)
    # The third call should now serve Beta (from the second refresh).
    assert [d.name for d in drives] == ["Beta"]
