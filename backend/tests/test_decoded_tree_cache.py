from __future__ import annotations

from app.drive.schemas import DriveTree
from app.identifiers import DriveId
from app.sidebar.cache import (
    DECODE_CACHE_MAX,
    clear_decoded_tree_cache,
    decoded_tree_cache_size,
    get_cached_decoded_tree,
    put_cached_decoded_tree,
)


def _tree() -> DriveTree:
    return DriveTree(drive_id=DriveId("d"), folders=[], files=[], files_complete=True)


def test_decoded_cache_hit_returns_same_object() -> None:
    clear_decoded_tree_cache()
    tree = _tree()
    drive_id = DriveId("drive-1")
    put_cached_decoded_tree((drive_id, "2026-06-15T00:00:00+00:00"), tree)
    assert get_cached_decoded_tree((drive_id, "2026-06-15T00:00:00+00:00")) is tree
    assert get_cached_decoded_tree((drive_id, "other-timestamp")) is None


def test_decoded_cache_evicts_oldest_beyond_max() -> None:
    clear_decoded_tree_cache()
    for i in range(DECODE_CACHE_MAX + 5):
        put_cached_decoded_tree((DriveId(f"drive-{i}"), "ts"), _tree())
    assert decoded_tree_cache_size() == DECODE_CACHE_MAX
    # the earliest inserted keys were evicted; the most recent survive
    assert get_cached_decoded_tree((DriveId("drive-0"), "ts")) is None
    last_drive_id = DriveId(f"drive-{DECODE_CACHE_MAX + 4}")
    assert get_cached_decoded_tree((last_drive_id, "ts")) is not None
    clear_decoded_tree_cache()
