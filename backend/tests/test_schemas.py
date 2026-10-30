from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.drive.schemas import (
    NAVIGATOR_SUPPORTED_MIME_TYPES,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
)
from app.extras.schemas import (
    MAX_HIDDEN_WORKSPACE_IDS,
    MAX_SORT_ORDER_ITEMS,
    PreferencesRead,
    PreferencesWrite,
    SortOrderBulkWrite,
    SortOrderWrite,
)
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


def test_drive_summary_defaults_to_shared_drive() -> None:
    d = DriveSummary(id=DriveId("d1"), name="Engineering")
    assert d.model_dump() == {"id": "d1", "is_personal": False, "name": "Engineering"}


def test_preferences_write_tracks_omitted_collection() -> None:
    p = PreferencesWrite()
    assert p.show_non_doc_files is None
    assert p.hidden_workspace_ids == []
    assert "hidden_workspace_ids" not in p.model_fields_set


def test_preferences_read_serializes_defaults() -> None:
    p = PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[])
    assert p.model_dump() == {"show_non_doc_files": True, "hidden_workspace_ids": []}


def test_bulk_sort_order_has_bounded_cardinality() -> None:
    items = [
        SortOrderWrite(drive_id=DriveId(f"drive-{index}"), sort_order=float(index))
        for index in range(MAX_SORT_ORDER_ITEMS)
    ]
    assert len(SortOrderBulkWrite(items=items).items) == MAX_SORT_ORDER_ITEMS

    with pytest.raises(ValidationError):
        SortOrderBulkWrite(items=[*items, items[0]])


def test_hidden_workspaces_have_bounded_cardinality() -> None:
    drive_ids = [DriveId(f"drive-{index}") for index in range(MAX_HIDDEN_WORKSPACE_IDS)]
    assert (
        len(PreferencesWrite(hidden_workspace_ids=drive_ids).hidden_workspace_ids)
        == MAX_HIDDEN_WORKSPACE_IDS
    )

    with pytest.raises(ValidationError):
        PreferencesWrite(hidden_workspace_ids=[*drive_ids, DriveId("overflow")])
