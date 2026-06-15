from __future__ import annotations

from app.drive.schemas import DriveTree
from app.sidebar.cache import _DECODE_CACHE_MAX, _decoded_get, _decoded_put, _decoded_trees


def _tree() -> DriveTree:
    return DriveTree(drive_id="d", folders=[], files=[], files_complete=True)


def test_decoded_cache_hit_returns_same_object() -> None:
    _decoded_trees.clear()
    tree = _tree()
    _decoded_put(("drive-1", "2026-06-15T00:00:00+00:00"), tree)
    assert _decoded_get(("drive-1", "2026-06-15T00:00:00+00:00")) is tree
    assert _decoded_get(("drive-1", "other-timestamp")) is None


def test_decoded_cache_evicts_oldest_beyond_max() -> None:
    _decoded_trees.clear()
    for i in range(_DECODE_CACHE_MAX + 5):
        _decoded_put((f"drive-{i}", "ts"), _tree())
    assert len(_decoded_trees) == _DECODE_CACHE_MAX
    # the earliest inserted keys were evicted; the most recent survive
    assert _decoded_get(("drive-0", "ts")) is None
    assert _decoded_get((f"drive-{_DECODE_CACHE_MAX + 4}", "ts")) is not None
    _decoded_trees.clear()
