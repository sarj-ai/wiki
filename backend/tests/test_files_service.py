"""Files service — error mapping and payload shape.

Full CRUD round-trips are covered at the router layer in integration
tests. Here we focus on the two bits that can silently go wrong:

- Drive HTTP error → HTTPException status mapping
- Unsupported mimeType rejection at create time
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from googleapiclient.errors import HttpError

from app.drive.schemas import DriveSummary
from app.files import service as files_service
from app.models.user import User


def _patch_member_drive(monkeypatch: pytest.MonkeyPatch, drive_id: str) -> None:
    """Pretend ``drive_id`` is a shared drive the test user is a member of.

    The files service now calls ``list_user_drives`` as part of its
    strict-scope check — any test that exercises a successful read or
    write path has to stub it out or the check walks all the way down
    into Drive credentials and blows up on the fake user.
    """

    async def _fake_list(_user: User) -> list[DriveSummary]:
        return [DriveSummary(id=drive_id, name="Stub Drive")]

    monkeypatch.setattr(files_service, "list_user_drives", _fake_list)


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
    u.email = "test@sarj.ai"
    u.google_id = "gid"
    u.token_version = 0
    return u


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "test"


def _make_http_error(status_code: int) -> HttpError:
    return HttpError(_FakeResp(status_code), b'{"error": "test"}')


async def test_create_rejects_unsupported_mime_type() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await files_service.create_file(
            _user(),
            parent_folder_id="p",
            name="x",
            mime_type="application/x-evil",
        )
    assert excinfo.value.status_code == 400


async def test_http_404_maps_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            raise _make_http_error(404)

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    with pytest.raises(HTTPException) as excinfo:
        await files_service.get_file(_user(), "missing")
    assert excinfo.value.status_code == 404


async def test_http_500_maps_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            raise _make_http_error(500)

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    with pytest.raises(HTTPException) as excinfo:
        await files_service.get_file(_user(), "x")
    assert excinfo.value.status_code == 502


async def test_create_success_returns_file_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def create(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "new-id",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["p"],
                "driveId": "d1",
                "modifiedTime": "2026-04-15T00:00:00Z",
            }

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    _patch_member_drive(monkeypatch, "d1")
    result = await files_service.create_file(
        _user(),
        parent_folder_id="p",
        name="Doc",
        mime_type="application/vnd.google-apps.document",
    )
    assert result.id == "new-id"
    assert result.parent_folder_id == "p"
    assert result.drive_id == "d1"


async def test_get_file_rejects_my_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Files without a driveId (My Drive) must be hidden from the Navigator."""

    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "my-drive-file",
                "name": "Personal",
                "mimeType": "application/vnd.google-apps.document",
                "parents": [],
                # No driveId — file lives in the user's My Drive.
            }

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    # list_user_drives shouldn't even be reached — the driveId is None.
    _patch_member_drive(monkeypatch, "irrelevant")

    with pytest.raises(HTTPException) as excinfo:
        await files_service.get_file(_user(), "my-drive-file")
    assert excinfo.value.status_code == 404


async def test_copy_file_rejects_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Folders return 400 before any Drive copy call is issued."""

    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "folder-id",
                "name": "Some Folder",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": ["p"],
                "driveId": "d1",
            }

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    _patch_member_drive(monkeypatch, "d1")

    with pytest.raises(HTTPException) as excinfo:
        await files_service.copy_file(_user(), "folder-id", "Copy of Some Folder")
    assert excinfo.value.status_code == 400


async def test_copy_file_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-folder copy returns the new file's metadata as a FileResponse."""
    calls: dict[str, Any] = {}

    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            self._mode = "get"
            return self

        def copy(self, **kwargs: Any) -> _FakeFiles:
            calls["copy_kwargs"] = kwargs
            self._mode = "copy"
            return self

        def execute(self) -> dict[str, Any]:
            if self._mode == "get":
                return {
                    "id": "src-id",
                    "name": "Architecture Notes",
                    "mimeType": "application/vnd.google-apps.document",
                    "parents": ["p"],
                    "driveId": "d1",
                }
            return {
                "id": "copy-id",
                "name": "Copy of Architecture Notes",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["p"],
                "driveId": "d1",
                "modifiedTime": "2026-04-15T00:00:00Z",
            }

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    _patch_member_drive(monkeypatch, "d1")

    result = await files_service.copy_file(_user(), "src-id", "Copy of Architecture Notes")
    assert result.id == "copy-id"
    assert result.name == "Copy of Architecture Notes"
    assert result.drive_id == "d1"
    assert calls["copy_kwargs"]["fileId"] == "src-id"
    assert calls["copy_kwargs"]["body"] == {"name": "Copy of Architecture Notes"}
    assert calls["copy_kwargs"]["supportsAllDrives"] is True


async def test_get_file_rejects_non_member_shared_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared-drive files still fail the check if the user isn't a member."""

    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "foreign-file",
                "name": "Not mine",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["foreign-drive"],
                "driveId": "foreign-drive",
            }

    class _FakeService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    monkeypatch.setattr(files_service, "get_drive_service", lambda _u: _FakeService())
    # User is only a member of "other-drive", not the file's drive.
    _patch_member_drive(monkeypatch, "other-drive")

    with pytest.raises(HTTPException) as excinfo:
        await files_service.get_file(_user(), "foreign-file")
    assert excinfo.value.status_code == 404
