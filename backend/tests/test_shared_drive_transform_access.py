from dataclasses import replace
from typing import Any

import pytest

from app.document_transform.errors import InPlaceNotAllowedError
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.schemas import DriveFileSource
from app.document_transform.source_access import native_source_access
from app.files.schemas import FileResponse
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId
from tests.test_document_transform import _branded_google_document
from tests.test_document_transform_header_provenance import _snapshot


def _metadata() -> dict[str, Any]:
    return {"driveId": "shared-drive-1", "capabilities": {"canEdit": True}, "trashed": False}


@pytest.mark.parametrize(
    "drive",
    [None, "", "with spaces", "x" * 257, False, {}],
    ids=["null", "empty", "space", "overlong", "boolean", "object"],
)
def test_explicit_malformed_shared_identity_cannot_become_personal(drive: object):
    access = native_source_access(_metadata() | {"driveId": drive, "ownedByMe": True})
    assert not access.can_edit
    assert not access.is_personal_drive
    assert access.shared_drive_id is None


@pytest.mark.parametrize(
    "changes",
    [
        {"trashed": True},
        {"trashed": None},
        {"trashed": "false"},
        {"capabilities": {}},
        {"capabilities": {"canEdit": "true"}},
        {"contentRestrictions": [{"readOnly": True}]},
        {"contentRestrictions": [{"readOnly": None}]},
        {"contentRestrictions": [{}]},
        {"contentRestrictions": None},
    ],
    ids=[
        "trashed",
        "trash_null",
        "trash_string",
        "capability_missing",
        "capability_string",
        "readonly",
        "restriction_null",
        "restriction_unknown",
        "restriction_container",
    ],
)
def test_noneditable_or_ambiguous_metadata_fails_closed(changes: dict[str, Any]):
    assert not native_source_access(_metadata() | changes).can_edit


@pytest.mark.parametrize("personal", [False, True])
def test_fresh_native_access_preserves_personal_and_shared_paths(*, personal: bool):
    metadata = _metadata()
    if personal:
        metadata.pop("driveId")
        metadata["ownedByMe"] = True
    access = native_source_access(metadata)
    assert access.can_edit
    assert access.is_personal_drive is personal
    assert access.owned_by_me is personal
    assert access.shared_drive_id == (None if personal else "shared-drive-1")
    document = _branded_google_document()
    document["documentId"] = "source"
    snapshot = replace(
        _snapshot(document),
        owned_by_me=access.owned_by_me,
        can_edit=access.can_edit,
        is_personal_drive=access.is_personal_drive,
        shared_drive_id=access.shared_drive_id,
    )
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    inspection = gateway._inspect_in_place_sync(snapshot, snapshot.title)
    assert inspection.output.file_id == document["documentId"]


@pytest.mark.parametrize(
    "changes",
    [
        {"can_edit": False, "shared_drive_id": DriveId("shared-drive-1")},
        {"owned_by_me": False, "is_personal_drive": True},
        {"owned_by_me": False, "is_personal_drive": False},
    ],
    ids=["shared_permission_lost", "unowned_personal", "unknown_location"],
)
def test_shared_support_does_not_weaken_other_eligibility(changes: dict[str, Any]):
    snapshot = replace(_snapshot(_branded_google_document()), **changes)
    gateway = object.__new__(GoogleDocumentGateway)
    with pytest.raises(InPlaceNotAllowedError):
        gateway._inspect_in_place_sync(snapshot, snapshot.title)


async def test_load_source_uses_fresh_capability_not_cached_file_access(
    monkeypatch: pytest.MonkeyPatch,
):
    metadata = _metadata() | {
        "name": "Source",
        "mimeType": "application/vnd.google-apps.document",
        "parents": ["folder"],
        "modifiedTime": "current",
    }
    files = object.__new__(GoogleFilesService)

    async def cached_file(file_id: FileId) -> FileResponse:
        return FileResponse(
            id=file_id,
            name="Source",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("folder"),
            drive_id=DriveId("shared-drive-1"),
            modified_time="cached",
        )

    monkeypatch.setattr(files, "get", cached_file)
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._files = files
    monkeypatch.setattr(gateway, "_files_get_sync", lambda _file_id: metadata)
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _file_id: _branded_google_document())
    source = DriveFileSource(
        kind="drive_file", file_id=FileId("source"), expected_modified_time="current"
    )
    allowed = await gateway.load_drive_source(source)
    assert allowed.shared_drive_id == "shared-drive-1"
    assert allowed.can_edit
    assert allowed.modified_time == "current"
    metadata["capabilities"] = {"canEdit": False}
    denied = await gateway.load_drive_source(source)
    assert not denied.can_edit
    with pytest.raises(InPlaceNotAllowedError):
        gateway._inspect_in_place_sync(denied, denied.title)
