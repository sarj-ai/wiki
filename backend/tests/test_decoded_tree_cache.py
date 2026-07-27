from __future__ import annotations

from app.drive.schemas import DriveTree
from app.sidebar.cache import (
    DECODE_CACHE_MAX,
    clear_public_title_decode_caches,
    decoded_tree_cache_size,
    get_cached_decoded_tree,
    put_cached_decoded_tree,
)


def _tree() -> DriveTree:
    return DriveTree(drive_id="d", folders=[], files=[], files_complete=True)


def test_decoded_cache_hit_returns_same_object() -> None:
    clear_public_title_decode_caches()
    tree = _tree()
    put_cached_decoded_tree(("drive-1", "2026-06-15T00:00:00+00:00"), tree)
    assert get_cached_decoded_tree(("drive-1", "2026-06-15T00:00:00+00:00")) is tree
    assert get_cached_decoded_tree(("drive-1", "other-timestamp")) is None


def test_decoded_cache_evicts_oldest_beyond_max() -> None:
    clear_public_title_decode_caches()
    for i in range(DECODE_CACHE_MAX + 5):
        put_cached_decoded_tree((f"drive-{i}", "ts"), _tree())
    assert decoded_tree_cache_size() == DECODE_CACHE_MAX
    # the earliest inserted keys were evicted; the most recent survive
    assert get_cached_decoded_tree(("drive-0", "ts")) is None
    assert get_cached_decoded_tree((f"drive-{DECODE_CACHE_MAX + 4}", "ts")) is not None
    clear_public_title_decode_caches()
