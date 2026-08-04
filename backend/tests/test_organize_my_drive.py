from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from app.drive.schemas import MIME_FOLDER, MIME_SHORTCUT
from scripts.organize_my_drive import (
    CapabilitySnapshot,
    DriveOrganizer,
    InventoryItem,
    InventorySnapshot,
    ManifestItem,
    build_manifest,
    classify_content,
    classify_item,
    permission_fingerprint,
)


ROOT_ID = "my-drive-root"


class _Request:
    def __init__(self, result: dict[str, Any] | bytes) -> None:
        self._result = result

    def execute(self) -> dict[str, Any] | bytes:
        return deepcopy(self._result)


class _About:
    def get(self, **_kwargs: Any) -> _Request:
        return _Request({"user": {"emailAddress": "nasr@sarj.ai"}})


class _Files:
    def __init__(self) -> None:
        self.metadata: dict[str, dict[str, Any]] = {
            "root": _raw_file(ROOT_ID, "My Drive", MIME_FOLDER, []),
            ROOT_ID: _raw_file(ROOT_ID, "My Drive", MIME_FOLDER, []),
            "folder": _raw_file("folder", "stt-audio", MIME_FOLDER, [ROOT_ID]),
            "doc": _raw_file(
                "doc", "PRD: Voice", "application/vnd.google-apps.document", [ROOT_ID]
            ),
            "shortcut": _raw_file(
                "shortcut", "Partner shortcut", MIME_SHORTCUT, [ROOT_ID], shortcut=True
            ),
            "nested": _raw_file("nested", "sample.mp3", "audio/mpeg", ["folder"]),
        }
        self.list_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> _Request:
        return _Request(self.metadata[str(kwargs["fileId"])])

    def list(self, **kwargs: Any) -> _Request:
        self.list_calls.append(kwargs)
        query = str(kwargs["q"])
        token = kwargs.get("pageToken")
        if f"'{ROOT_ID}' in parents" in query:
            if token is None:
                return _Request(
                    {
                        "files": [self.metadata["folder"], self.metadata["doc"]],
                        "nextPageToken": "second",
                    }
                )
            return _Request({"files": [self.metadata["shortcut"]]})
        if "'folder' in parents" in query:
            return _Request({"files": [self.metadata["nested"]]})
        return _Request({"files": []})

    def update(self, **kwargs: Any) -> _Request:
        file_id = str(kwargs.pop("fileId"))
        add_parents = str(kwargs.pop("addParents"))
        remove_parents = str(kwargs.pop("removeParents"))
        self.update_calls.append(
            {
                "fileId": file_id,
                "addParents": add_parents,
                "removeParents": remove_parents,
                **kwargs,
            }
        )
        self.metadata[file_id]["parents"] = [add_parents]
        return _Request({"id": file_id, "parents": [add_parents], "version": "2"})


class _Client:
    def __init__(self) -> None:
        self._files = _Files()

    def about(self) -> _About:
        return _About()

    def files(self) -> _Files:
        return self._files


def _manifest_item(**overrides: object) -> ManifestItem:
    values: dict[str, object] = {
        "file_id": "doc",
        "name": "PRD: Voice",
        "mime_type": "application/vnd.google-apps.document",
        "current_parent_id": ROOT_ID,
        "destination_path": "Work/Product & Engineering",
        "action": "MOVE",
        "confidence": 0.9,
        "reason": "test",
        "shared": False,
        "subtree_shared": False,
        "subtree_owned": True,
        "subtree_complete": True,
    }
    values.update(overrides)
    return ManifestItem.model_validate(values)


def test_inventory_walks_all_pages_and_never_follows_shortcuts() -> None:
    client = _Client()
    snapshot = DriveOrganizer(cast("Any", client), "nasr@sarj.ai").inventory()

    assert snapshot.complete
    assert snapshot.root_item_count == 3
    assert {item.path for item in snapshot.items} == {
        "PRD: Voice",
        "Partner shortcut",
        "stt-audio",
        "stt-audio/sample.mp3",
    }
    assert len(client._files.list_calls) == 3
    assert all(call["corpora"] == "user" for call in client._files.list_calls)
    assert all(call["includeItemsFromAllDrives"] is False for call in client._files.list_calls)
    assert not any("'shortcut' in parents" in str(call["q"]) for call in client._files.list_calls)


def test_manifest_routes_private_owned_item_and_reviews_shared_subtree() -> None:
    prd = _item("prd", "PRD: Registration", shared=False)
    audio = _item("audio", "stt-audio", mime_type=MIME_FOLDER, shared=True)
    child = _item(
        "audio-child",
        "sample.mp3",
        mime_type="audio/mpeg",
        parent_ids=["audio"],
        root_child_id="audio",
    )
    snapshot = _snapshot([prd, audio, child])

    manifest = build_manifest(snapshot, inventory_sha256="abc")
    decisions = {item.file_id: item for item in manifest.items}

    assert decisions["prd"].action == "MOVE"
    assert decisions["prd"].destination_path == "Work/Product & Engineering"
    assert decisions["audio"].action == "REVIEW"
    assert decisions["audio"].subtree_shared


def test_manifest_skips_non_owned_and_anomalous_parent_items() -> None:
    non_owned = _item("other", "Website Notes", owned_by_me=False)
    no_parent = _item("orphan", "Sarj logo", parent_ids=[])
    snapshot = _snapshot([non_owned, no_parent], root_item_count=2)
    snapshot.items[1].parent_ids = [ROOT_ID, "legacy-parent"]

    manifest = build_manifest(snapshot, inventory_sha256="abc")
    decisions = {item.file_id: item.action for item in manifest.items}

    assert decisions == {"orphan": "SKIP", "other": "SKIP"}


def test_existing_taxonomy_folder_is_kept() -> None:
    work = _item("work", "Work", mime_type=MIME_FOLDER)

    result = classify_item(work)

    assert result.destination_path is None
    assert result.confidence == 1


def test_audited_private_ambiguous_items_can_be_staged_in_review() -> None:
    private = _item("private", "Untitled document")
    shared = _item("shared", "Untitled document", shared=True)

    manifest = build_manifest(
        _snapshot([private, shared]),
        inventory_sha256="abc",
        move_private_review=True,
    )
    decisions = {item.file_id: item for item in manifest.items}

    assert decisions["private"].action == "MOVE"
    assert decisions["private"].destination_path == "Review"
    assert decisions["shared"].action == "REVIEW"


def test_ambiguous_content_is_classified_without_returning_source_text() -> None:
    result = classify_content("Employee performance review and recruiting plan")

    assert result is not None
    assert result.destination_path == "Work/Company Operations"
    assert "Employee" not in result.reason


def test_permission_fingerprint_is_order_independent_and_change_sensitive() -> None:
    owner = {"id": "owner", "type": "user", "role": "owner", "emailAddress": "n@sarj.ai"}
    reader = {"id": "reader", "type": "user", "role": "reader", "emailAddress": "a@sarj.ai"}

    first = permission_fingerprint([owner, reader])
    second = permission_fingerprint([reader, owner])
    changed = permission_fingerprint([owner, {**reader, "role": "writer"}])

    assert first == second
    assert first != changed


def test_inventory_snapshot_round_trip_preserves_capabilities() -> None:
    snapshot = _snapshot([_item("prd", "PRD: Registration")])

    reloaded = InventorySnapshot.model_validate_json(snapshot.model_dump_json())

    assert reloaded.items[0].capabilities.can_move_item_within_drive


def test_apply_move_rejects_parent_drift(tmp_path: Path) -> None:
    client = _Client()
    organizer = DriveOrganizer(cast("Any", client), "nasr@sarj.ai")
    entry = _manifest_item(
        current_parent_id="unexpected-parent",
    )

    with pytest.raises(RuntimeError, match="Parent conflict"):
        organizer.apply_move(
            entry,
            target_id="folder",
            expected_permissions={},
            run_id="test-run",
            journal=tmp_path / "journal.jsonl",
        )

    assert not client._files.update_calls


def test_apply_folder_move_rescans_and_rejects_shared_descendant(tmp_path: Path) -> None:
    client = _Client()
    client._files.metadata["nested"]["shared"] = True
    organizer = DriveOrganizer(cast("Any", client), "nasr@sarj.ai")
    entry = _manifest_item(
        file_id="folder",
        name="stt-audio",
        mime_type=MIME_FOLDER,
    )

    with pytest.raises(RuntimeError, match="contains shared item"):
        organizer.apply_move(
            entry,
            target_id="doc",
            expected_permissions={},
            run_id="test-run",
            journal=tmp_path / "journal.jsonl",
        )

    assert not client._files.update_calls


def _raw_file(
    file_id: str,
    name: str,
    mime_type: str,
    parents: list[str],
    *,
    shortcut: bool = False,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "parents": parents,
        "ownedByMe": True,
        "shared": False,
        "trashed": False,
        "capabilities": {"canMoveItemWithinDrive": True, "canAddChildren": True},
        "permissions": [
            {
                "id": "owner",
                "type": "user",
                "role": "owner",
                "emailAddress": "nasr@sarj.ai",
            }
        ],
    }
    if shortcut:
        raw["shortcutDetails"] = {
            "targetId": "external-target",
            "targetMimeType": "application/vnd.google-apps.document",
        }
    return raw


def _item(
    file_id: str,
    name: str,
    *,
    mime_type: str = "application/vnd.google-apps.document",
    parent_ids: list[str] | None = None,
    root_child_id: str | None = None,
    owned_by_me: bool = True,
    shared: bool = False,
) -> InventoryItem:
    return InventoryItem(
        id=file_id,
        name=name,
        mime_type=mime_type,
        parent_ids=parent_ids if parent_ids is not None else [ROOT_ID],
        owned_by_me=owned_by_me,
        shared=shared,
        trashed=False,
        capabilities=CapabilitySnapshot.model_validate(
            {"canMoveItemWithinDrive": True, "canAddChildren": mime_type == MIME_FOLDER}
        ),
        permissions=[],
        permission_fingerprint=permission_fingerprint([]),
        path=name,
        depth=1,
        root_child_id=root_child_id or file_id,
    )


def _snapshot(
    items: list[InventoryItem], *, root_item_count: int | None = None
) -> InventorySnapshot:
    return InventorySnapshot(
        generated_at="2026-08-02T00:00:00+00:00",
        account_email="nasr@sarj.ai",
        root_id=ROOT_ID,
        root_item_count=root_item_count if root_item_count is not None else len(items),
        complete=True,
        errors=[],
        items=items,
    )
