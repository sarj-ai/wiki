from __future__ import annotations

import pytest

from app.drive import probe_cache
from app.drive.schemas import ShortcutProbe
from app.identifiers import DriveId, FileId


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    probe_cache.clear()


def test_unknown_targets_are_absent_from_the_result() -> None:
    assert probe_cache.get_fresh("alice", [FileId("t1"), FileId("t2")]) == {}


def test_store_then_get_roundtrips_positive_and_negative_outcomes() -> None:
    probe_cache.store(
        "alice",
        {
            FileId("t-ok"): ShortcutProbe(accessible=True, target_drive_id=DriveId("eng")),
            FileId("t-403"): ShortcutProbe(accessible=False),
            FileId("t-gone"): None,
        },
    )
    fresh = probe_cache.get_fresh(
        "alice", [FileId("t-ok"), FileId("t-403"), FileId("t-gone"), FileId("t-unknown")]
    )
    assert fresh == {
        FileId("t-ok"): ShortcutProbe(accessible=True, target_drive_id=DriveId("eng")),
        FileId("t-403"): ShortcutProbe(accessible=False),
        FileId("t-gone"): None,
    }
    assert "t-unknown" not in fresh


def test_entries_are_scoped_per_user() -> None:
    probe_cache.store("alice", {FileId("t1"): ShortcutProbe(accessible=True)})
    assert probe_cache.get_fresh("bob", [FileId("t1")]) == {}


def test_expired_entries_are_treated_as_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(probe_cache.time, "monotonic", lambda: clock["now"])

    probe_cache.store("alice", {FileId("t1"): ShortcutProbe(accessible=True)})
    assert probe_cache.get_fresh("alice", [FileId("t1")]) != {}

    clock["now"] += probe_cache._TTL_SECONDS + 1
    assert probe_cache.get_fresh("alice", [FileId("t1")]) == {}


def test_store_prunes_expired_entries_past_the_sweep_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(probe_cache.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(probe_cache, "_MAX_ENTRIES", 2)

    probe_cache.store("alice", {FileId("t1"): None, FileId("t2"): None})
    clock["now"] += probe_cache._TTL_SECONDS + 1
    probe_cache.store("alice", {FileId("t3"): None})
    assert probe_cache._entries.keys() == {("alice", "t3")}
