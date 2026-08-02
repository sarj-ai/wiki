from __future__ import annotations

from app.drive.schemas import (
    NAVIGATOR_SUPPORTED_MIME_TYPES,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
)
from app.extras.schemas import PreferencesRead, PreferencesWrite
from app.identifiers import DriveId, FileId


def test_drive_node_shortcut_defaults() -> None:
    node = DriveNode(
        id=FileId("x"),
        name="X",
        mime_type="application/vnd.google-apps.folder",
        parent_id=None,
        modified_time=None,
    )
    assert node.shortcut_target_id is None
    assert node.shortcut_inaccessible is False


def test_drive_tree_empty() -> None:
    tree = DriveTree(drive_id=DriveId("d1"), folders=[], files=[], files_complete=True)
    assert tree.folders == []
    assert tree.files == []
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


def test_search_result_defaults_shortcut_accessible() -> None:
    r = SearchResult(
        id=FileId("x"),
        name="X",
        mime_type="t",
        drive_id=None,
        drive_name=None,
        parent_id=None,
        modified_time=None,
    )
    assert r.shortcut_inaccessible is False
    assert r.shortcut_target_id is None


def test_drive_summary_is_minimal() -> None:
    d = DriveSummary(id=DriveId("d1"), name="Engineering")
    assert d.model_dump() == {"id": "d1", "name": "Engineering"}


def test_preferences_write_tracks_omitted_collection() -> None:
    p = PreferencesWrite()
    assert p.show_non_doc_files is None
    assert p.hidden_workspace_ids == []
    assert "hidden_workspace_ids" not in p.model_fields_set


def test_preferences_read_serializes_defaults() -> None:
    p = PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[])
    assert p.model_dump() == {"show_non_doc_files": True, "hidden_workspace_ids": []}
