"""Unit tests for the per-user drives.list cache."""

from __future__ import annotations

import pytest

from app.drive import drives_cache
from app.drive.schemas import DriveSummary


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Each test starts with an empty cache so module-level state
    doesn't leak across cases."""
    drives_cache.clear()


def _drives() -> list[DriveSummary]:
    return [DriveSummary(id="d1", name="One"), DriveSummary(id="d2", name="Two")]


def test_lookup_returns_none_for_unknown_user() -> None:
    assert drives_cache.lookup("alice") is None


def test_store_then_lookup_returns_cached_drives() -> None:
    drives = _drives()
    drives_cache.store("alice", drives)
    assert drives_cache.lookup("alice") == drives


def test_lookup_returns_independent_copy() -> None:
    """Mutating the returned list must not corrupt the cached entry —
    the cache stores tuples internally for exactly this reason.
    """
    drives_cache.store("alice", _drives())
    cached = drives_cache.lookup("alice")
    assert cached is not None
    cached.clear()
    refetched = drives_cache.lookup("alice")
    assert refetched is not None
    assert len(refetched) == 2


def test_lookup_returns_none_after_ttl_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale entry behaves identically to a missing one — callers
    refetch and overwrite. Time is faked via monkeypatch so the test
    runs instantly instead of sleeping the TTL."""
    base = 1_000_000.0
    now = base
    monkeypatch.setattr(drives_cache.time, "monotonic", lambda: now)
    drives_cache.store("alice", _drives())
    assert drives_cache.lookup("alice") is not None

    now = base + drives_cache._TTL_SECONDS - 0.1  # within TTL
    assert drives_cache.lookup("alice") is not None

    now = base + drives_cache._TTL_SECONDS  # exactly at TTL — counts as stale
    assert drives_cache.lookup("alice") is None


def test_invalidate_drops_only_the_targeted_user() -> None:
    drives_cache.store("alice", _drives())
    drives_cache.store("bob", [DriveSummary(id="x", name="Other")])
    drives_cache.invalidate("alice")
    assert drives_cache.lookup("alice") is None
    assert drives_cache.lookup("bob") is not None


def test_invalidate_unknown_user_is_a_noop() -> None:
    drives_cache.invalidate("ghost")  # must not raise


def test_store_overwrites_previous_entry() -> None:
    drives_cache.store("alice", _drives())
    new_drives = [DriveSummary(id="d3", name="Three")]
    drives_cache.store("alice", new_drives)
    assert drives_cache.lookup("alice") == new_drives


def test_clear_drops_every_entry() -> None:
    drives_cache.store("alice", _drives())
    drives_cache.store("bob", _drives())
    drives_cache.clear()
    assert drives_cache.lookup("alice") is None
    assert drives_cache.lookup("bob") is None
