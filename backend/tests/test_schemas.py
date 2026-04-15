"""Lightweight schema validation tests.

Covers the bits where Pydantic's default behavior isn't obvious:
optional fields defaulting to None, and the flat-list shape the
frontend depends on.
"""

from __future__ import annotations

from app.drive.schemas import (
    NAVIGATOR_SUPPORTED_MIME_TYPES,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
)
from app.extras.schemas import PreferencesRead, PreferencesWrite


def test_drive_node_defaults() -> None:
    node = DriveNode(id="x", name="X", mime_type="application/vnd.google-apps.folder")
    assert node.parent_id is None
    assert node.modified_time is None


def test_drive_tree_empty() -> None:
    tree = DriveTree(drive_id="d1")
    assert tree.folders == []
    assert tree.files == []
    # Round-trip through JSON stays stable — used by the JSONB cache.
    restored = DriveTree.model_validate(tree.model_dump(mode="json"))
    assert restored.drive_id == "d1"


def test_supported_mime_types_include_all_workspace_apps() -> None:
    expected = {
        "application/vnd.google-apps.folder",
        "application/vnd.google-apps.document",
        "application/vnd.google-apps.spreadsheet",
        "application/vnd.google-apps.presentation",
        "application/vnd.google-apps.drawing",
        "application/vnd.google-apps.form",
    }
    assert expected.issubset(set(NAVIGATOR_SUPPORTED_MIME_TYPES))


def test_search_result_round_trip() -> None:
    r = SearchResult(id="x", name="X", mime_type="t")
    assert r.drive_id is None
    assert r.parent_id is None


def test_drive_summary_is_minimal() -> None:
    d = DriveSummary(id="d1", name="Engineering")
    assert d.model_dump() == {"id": "d1", "name": "Engineering"}


def test_preferences_write_partial_fields_default_none() -> None:
    p = PreferencesWrite()
    assert p.show_non_doc_files is None
    assert p.hidden_workspace_ids is None


def test_preferences_read_serializes_defaults() -> None:
    p = PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[])
    assert p.model_dump() == {"show_non_doc_files": True, "hidden_workspace_ids": []}
