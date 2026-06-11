"""FilesService — error mapping and strict-scope checks.

Exercises ``GoogleFilesService`` directly with a fake ``DriveResource``
stubbed in via ``GoogleDriveService.build_drive_client`` and an injected
fake ``DriveService`` that returns a canned member-drive list. The
service raises typed :class:`FilesError` subclasses; HTTP-status mapping
happens at the app boundary and is exercised in router-level tests.
"""

from __future__ import annotations

from typing import Any
import uuid

from googleapiclient.errors import HttpError
import pytest

from app.drive.schemas import DriveNode, DriveSummary, DriveTree, SearchResult, SnippetResult
from app.drive.service import DriveService, GoogleDriveService
from app.files.errors import (
    DriveUpstreamError,
    FileNotInScopeError,
    FolderCopyNotSupportedError,
    UnsupportedMimeError,
)
from app.files.service import GoogleFilesService
from app.models.user import User


class _FakeDriveService(DriveService):
    """Fake ``DriveService`` that only answers ``list_drives``."""

    def __init__(self, drives: list[DriveSummary]) -> None:
        self._drives = drives

    async def list_drives(self) -> list[DriveSummary]:
        return list(self._drives)

    async def walk_drive_folders(self, drive_id: str) -> DriveTree:
        raise NotImplementedError

    async def list_folder_children(self, folder_id: str) -> list[DriveNode]:
        raise NotImplementedError

    async def search_drive(
        self, drive_id: str, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        raise NotImplementedError

    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        raise NotImplementedError

    async def fetch_snippet(
        self, file_id: str, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        raise NotImplementedError


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
    u.email = "test@sarj.ai"
    u.google_id = "gid"
    u.token_version = 0
    return u


def _svc(member_drive_id: str | None = None) -> GoogleFilesService:
    drives = [DriveSummary(id=member_drive_id, name="Stub Drive")] if member_drive_id else []
    return GoogleFilesService(user=_user(), drive=_FakeDriveService(drives))


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "test"


def _make_http_error(status_code: int) -> HttpError:
    return HttpError(_FakeResp(status_code), b'{"error": "test"}')  # type: ignore[arg-type]


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(
        GoogleDriveService,
        "build_drive_client",
        staticmethod(lambda _u: client),
    )


async def test_create_rejects_unsupported_mime_type() -> None:
    with pytest.raises(UnsupportedMimeError):
        await _svc("d1").create(
            parent_folder_id="p",
            name="x",
            mime_type="application/x-evil",
        )


async def test_http_404_surfaces_as_drive_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            raise _make_http_error(404)

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(DriveUpstreamError) as excinfo:
        await _svc("d1").get("missing")
    assert excinfo.value.http_status == 404


async def test_http_500_maps_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            raise _make_http_error(500)

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(DriveUpstreamError) as excinfo:
        await _svc("d1").get("x")
    assert excinfo.value.http_status == 502


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

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    result = await _svc("d1").create(
        parent_folder_id="p",
        name="Doc",
        mime_type="application/vnd.google-apps.document",
    )
    assert result.id == "new-id"
    assert result.parent_folder_id == "p"
    assert result.drive_id == "d1"


async def test_create_does_not_retry_ambiguous_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"create": 0}

    class _FakeFiles:
        def create(self, **_: Any) -> _FakeFiles:
            return self

        def execute(self) -> dict[str, Any]:
            attempts["create"] += 1
            raise _make_http_error(500)

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(DriveUpstreamError):
        await _svc("d1").create(
            parent_folder_id="p",
            name="Doc",
            mime_type="application/vnd.google-apps.document",
        )
    assert attempts["create"] == 1


async def test_move_reuses_prefetched_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    ops: list[dict[str, Any]] = []

    class _FakeFiles:
        def __init__(self) -> None:
            self._result: dict[str, Any] = {}

        def get(self, **kwargs: Any) -> _FakeFiles:
            ops.append({"_op": "get", **kwargs})
            self._result = {
                "id": "f",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["old-parent"],
                "driveId": "d1",
            }
            return self

        def update(self, **kwargs: Any) -> _FakeFiles:
            ops.append({"_op": "update", **kwargs})
            self._result = {
                "id": "f",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["new-parent"],
                "driveId": "d1",
            }
            return self

        def execute(self) -> dict[str, Any]:
            return self._result

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    result = await _svc("d1").move("f", "new-parent")
    assert result.moved.parent_folder_id == "new-parent"

    gets = [o for o in ops if o["_op"] == "get"]
    updates = [o for o in ops if o["_op"] == "update"]
    assert len(gets) == 1
    assert len(updates) == 1
    assert updates[0]["removeParents"] == "old-parent"
    assert updates[0]["addParents"] == "new-parent"


async def test_get_rejects_my_drive(monkeypatch: pytest.MonkeyPatch) -> None:
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

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(FileNotInScopeError):
        await _svc("irrelevant").get("my-drive-file")


async def test_copy_rejects_folder(monkeypatch: pytest.MonkeyPatch) -> None:
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

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(FolderCopyNotSupportedError):
        await _svc("d1").copy("folder-id", "Copy of Some Folder")


async def test_copy_success(monkeypatch: pytest.MonkeyPatch) -> None:
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

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    result = await _svc("d1").copy("src-id", "Copy of Architecture Notes")
    assert result.id == "copy-id"
    assert result.name == "Copy of Architecture Notes"
    assert result.drive_id == "d1"
    assert calls["copy_kwargs"]["fileId"] == "src-id"
    assert calls["copy_kwargs"]["body"] == {"name": "Copy of Architecture Notes"}
    assert calls["copy_kwargs"]["supportsAllDrives"] is True


async def test_get_rejects_non_member_shared_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    # User is only a member of "other-drive", not the file's drive.
    with pytest.raises(FileNotInScopeError):
        await _svc("other-drive").get("foreign-file")
