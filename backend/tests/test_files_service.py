from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple, Self, override
import uuid

from googleapiclient.errors import HttpError
from httplib2 import Response
import pytest

from app.drive.schemas import (
    DriveNode,
    DrivePermissionsPage,
    DriveSummary,
    DriveTree,
    SearchResult,
    SnippetResult,
)
from app.drive.service import DriveService, GoogleDriveService
from app.files.errors import (
    CrossDriveMoveNotSupportedError,
    DriveUpstreamError,
    FileNotInScopeError,
    FolderCopyNotSupportedError,
    UnsupportedMimeError,
)
from app.files.schemas import FileResponse
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, GoogleUserId, TaskId, UserId
from app.models.user import User


class _FakeDriveService(DriveService):
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

    async def list_drive_permissions(
        self,
        drive_id: DriveId,
        *,
        page_token: str | None = None,
    ) -> DrivePermissionsPage:
        return DrivePermissionsPage(
            access_model="shared_drive_membership",
            drive_id=drive_id,
            next_page_token=page_token,
            permissions=[],
        )


def _user() -> User:
    u = User()
    u.id = UserId(uuid.uuid7())
    u.email = "test@sarj.ai"
    u.google_id = GoogleUserId("gid")
    u.token_version = 0
    return u


def _svc(member_drive_id: str | None = None) -> GoogleFilesService:
    drives = (
        [DriveSummary(id=DriveId(member_drive_id), name="Stub Drive")] if member_drive_id else []
    )
    return GoogleFilesService(user=_user(), drive=_FakeDriveService(drives))


def _make_http_error(status_code: int) -> HttpError:
    response = Response({"reason": "test", "status": str(status_code), "retry-after": "0"})
    return HttpError(response, b'{"error": "test"}')


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(
        GoogleDriveService,
        "build_drive_client",
        staticmethod(lambda _u: client),
    )


async def test_create_rejects_unsupported_mime_type() -> None:
    with pytest.raises(UnsupportedMimeError):
        await _svc("d1").create(
            parent_folder_id=FileId("p"),
            name="x",
            mime_type="application/x-evil",
        )


@pytest.mark.parametrize(
    ("google_status", "file_id", "expected_status"),
    [(404, "missing", 404), (500, "x", 502)],
    ids=["not-found", "upstream-failure"],
)
async def test_http_errors_map_to_drive_upstream(
    monkeypatch: pytest.MonkeyPatch,
    google_status: int,
    file_id: FileId,
    expected_status: int,
) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> Self:
            return self

        def execute(self) -> dict[str, Any]:
            raise _make_http_error(google_status)

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(DriveUpstreamError) as excinfo:
        await _svc("d1").get(FileId(file_id))
    assert excinfo.value.http_status == expected_status


async def test_create_success_returns_file_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def create(self, **_: Any) -> Self:
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
        parent_folder_id=FileId("p"),
        name="Doc",
        mime_type="application/vnd.google-apps.document",
    )
    assert result.id == "new-id"
    assert result.parent_folder_id == "p"
    assert result.drive_id == "d1"


async def test_can_edit_uses_drive_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> Self:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "capabilities": {"canEdit": True},
                "driveId": "d1",
                "id": "editable",
                "mimeType": "application/vnd.google-apps.document",
                "name": "Editable",
            }

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    assert await _svc("d1").can_edit(FileId("editable")) is True


async def test_get_rejects_trashed_file(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> Self:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "trashed",
                "mimeType": "application/vnd.google-apps.document",
                "name": "Trashed",
                "trashed": True,
            }

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(FileNotInScopeError):
        await _svc("d1").get(FileId("trashed"))


async def test_get_accessible_many_uses_one_google_batch_and_omits_denied_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_executions = 0

    class _Request(NamedTuple):
        file_id: str

    class _FakeFiles:
        def get(self, **kwargs: Any) -> _Request:
            return _Request(kwargs["fileId"])

    class _Batch:
        def __init__(self, callback: Any) -> None:
            self.callback = callback
            self.requests: list[tuple[str, _Request]] = []

        def add(self, request: _Request, *, request_id: str) -> None:
            self.requests.append((request_id, request))

        def execute(self) -> None:
            nonlocal batch_executions
            batch_executions += 1
            for request_id, request in self.requests:
                if request.file_id == "denied":
                    self.callback(request_id, None, _make_http_error(403))
                    continue
                self.callback(
                    request_id,
                    {
                        "driveId": "d1",
                        "id": request.file_id,
                        "mimeType": "application/vnd.google-apps.document",
                        "name": f"Document {request.file_id}",
                        "trashed": request.file_id == "trashed",
                    },
                    None,
                )

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

        def new_batch_http_request(self, *, callback: Any) -> _Batch:
            return _Batch(callback)

    _patch_client(monkeypatch, _FakeClient())

    result = await _svc("d1").get_accessible_many(
        [FileId("first"), FileId("denied"), FileId("trashed"), FileId("second")]
    )

    assert list(result) == ["first", "second"]
    assert batch_executions == 1


async def test_qa_corpus_metadata_batch_omits_unbounded_description(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_fields: list[str] = []

    class _Request(NamedTuple):
        file_id: str

    class _FakeFiles:
        def get(self, **kwargs: Any) -> _Request:
            requested_fields.append(str(kwargs["fields"]))
            return _Request(kwargs["fileId"])

    class _Batch:
        def __init__(self, callback: Any) -> None:
            self.callback = callback
            self.requests: list[tuple[str, _Request]] = []

        def add(self, request: _Request, *, request_id: str) -> None:
            self.requests.append((request_id, request))

        def execute(self) -> None:
            for request_id, request in self.requests:
                self.callback(
                    request_id,
                    {
                        "driveId": "d1",
                        "id": request.file_id,
                        "mimeType": "application/vnd.google-apps.document",
                        "name": "Document",
                        "parents": ["folder"],
                        "modifiedTime": "2026-09-08T12:00:00Z",
                        "version": "7",
                    },
                    None,
                )

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

        def new_batch_http_request(self, *, callback: Any) -> _Batch:
            return _Batch(callback)

    _patch_client(monkeypatch, _FakeClient())
    result = await _svc("d1").get_qa_corpus_metadata_many([FileId("first")])

    assert result[FileId("first")].version == "7"
    assert requested_fields
    assert all("version" in fields for fields in requested_fields)
    assert all("description" not in fields for fields in requested_fields)


def test_internal_provider_version_is_excluded_from_public_file_response() -> None:
    response = FileResponse(
        id=FileId("doc"),
        name="Doc",
        mime_type="application/vnd.google-apps.document",
        parent_folder_id=FileId("folder"),
        drive_id=DriveId("drive"),
        modified_time=None,
        version="provider-version",
    )

    serialized = response.model_dump(mode="json")

    assert "version" not in serialized
    assert serialized["drive_id"] == "drive"


async def test_get_accessible_many_resolves_membership_as_one_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Request(NamedTuple):
        file_id: str

    class _FakeFiles:
        def get(self, **kwargs: Any) -> _Request:
            return _Request(kwargs["fileId"])

    class _Batch:
        def __init__(self, callback: Any) -> None:
            self.callback = callback
            self.requests: list[tuple[str, _Request]] = []

        def add(self, request: _Request, *, request_id: str) -> None:
            self.requests.append((request_id, request))

        def execute(self) -> None:
            for request_id, request in self.requests:
                self.callback(
                    request_id,
                    {
                        "driveId": "reported-drive",
                        "id": request.file_id,
                        "mimeType": "application/vnd.google-apps.document",
                        "name": request.file_id,
                    },
                    None,
                )

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

        def new_batch_http_request(self, *, callback: Any) -> _Batch:
            return _Batch(callback)

    class _BatchMembershipDrive(_FakeDriveService):
        def __init__(self) -> None:
            super().__init__([])
            self.batch_calls: list[dict[FileId, DriveId | None]] = []

        @override
        async def resolve_member_drive(
            self,
            file_id: FileId,
            drive_id: DriveId | None,
            *,
            parent_id: FileId | None = None,
        ) -> DriveId | None:
            del parent_id
            raise AssertionError

        @override
        async def resolve_member_drives(
            self,
            files: Mapping[FileId, DriveId | None],
            *,
            parent_ids: Mapping[FileId, FileId | None] | None = None,
        ) -> dict[FileId, DriveId]:
            del parent_ids
            self.batch_calls.append(dict(files))
            return {file_id: DriveId("member-drive") for file_id in files}

    _patch_client(monkeypatch, _FakeClient())
    drive = _BatchMembershipDrive()
    service = GoogleFilesService(user=_user(), drive=drive)

    result = await service.get_accessible_many([FileId("one"), FileId("two")])

    assert drive.batch_calls == [
        {FileId("one"): DriveId("reported-drive"), FileId("two"): DriveId("reported-drive")}
    ]
    assert {file.drive_id for file in result.values()} == {"member-drive"}


async def test_create_does_not_retry_ambiguous_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"create": 0}

    class _FakeFiles:
        def create(self, **_: Any) -> Self:
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
            parent_folder_id=FileId("p"),
            name="Doc",
            mime_type="application/vnd.google-apps.document",
        )
    assert attempts["create"] == 1


async def test_task_recovery_uses_user_corpus_for_my_drive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_params: list[dict[str, Any]] = []

    class _PersonalDriveService(_FakeDriveService):
        async def resolve_member_drive(
            self,
            file_id: FileId,
            drive_id: DriveId | None,
            *,
            parent_id: FileId | None = None,
        ) -> DriveId | None:
            if file_id == FileId("recovered") and drive_id is None:
                return DriveId("personal-root")
            return await super().resolve_member_drive(file_id, drive_id, parent_id=parent_id)

    class _FakeFiles:
        def list(self, **kwargs: Any) -> Self:
            list_params.append(kwargs)
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "files": [
                    {
                        "id": "shared-duplicate",
                        "name": "Wrong workspace",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["shared-root"],
                        "driveId": "shared-drive",
                    },
                    {
                        "id": "recovered",
                        "name": "Recovered",
                        "mimeType": "application/vnd.google-apps.document",
                        "parents": ["personal-root"],
                    },
                ]
            }

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    drive = _PersonalDriveService(
        [DriveSummary(id=DriveId("personal-root"), name="My Drive", is_personal=True)]
    )
    service = GoogleFilesService(user=_user(), drive=drive)

    result = await service.find_by_task(DriveId("personal-root"), TaskId(uuid.uuid7()))

    assert result is not None
    assert result.drive_id == DriveId("personal-root")
    assert list_params[0]["corpora"] == "user"
    assert "driveId" not in list_params[0]


@pytest.mark.parametrize("already_moved", [False, True])
async def test_move_reuses_prefetched_parent(
    monkeypatch: pytest.MonkeyPatch,
    *,
    already_moved: bool,
) -> None:
    ops: list[dict[str, Any]] = []

    class _FakeFiles:
        def __init__(self) -> None:
            self._result: dict[str, Any] = {}

        def get(self, **kwargs: Any) -> Self:
            ops.append({"_op": "get", **kwargs})
            if kwargs["fileId"] == "new-parent":
                self._result = {
                    "capabilities": {"canAddChildren": True},
                    "id": "new-parent",
                    "name": "Destination",
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": ["d1"],
                    "driveId": "d1",
                }
            else:
                self._result = {
                    "id": "f",
                    "name": "Doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "parents": ["new-parent" if already_moved else "old-parent"],
                    "driveId": "d1",
                }
            return self

        def update(self, **kwargs: Any) -> Self:
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
    result = await _svc("d1").move(FileId("f"), FileId("new-parent"))
    assert result.moved.parent_folder_id == "new-parent"

    gets = [o for o in ops if o["_op"] == "get"]
    updates = [o for o in ops if o["_op"] == "update"]
    assert len(gets) == 2
    if already_moved:
        assert updates == []
        assert result.moved == result.source
        return
    assert len(updates) == 1
    assert updates[0]["removeParents"] == "old-parent"
    assert updates[0]["addParents"] == "new-parent"


async def test_cross_drive_file_move_preflights_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[str] = []

    class _Request:
        def __init__(self, result: dict[str, Any]) -> None:
            self.result = result

        def execute(self) -> dict[str, Any]:
            return self.result

    class _FakeFiles:
        def get(self, **kwargs: Any) -> _Request:
            file_id = kwargs["fileId"]
            operations.append(f"get:{file_id}")
            if file_id == "destination":
                return _Request(
                    {
                        "capabilities": {"canAddChildren": True},
                        "driveId": "d2",
                        "id": "destination",
                        "mimeType": "application/vnd.google-apps.folder",
                        "name": "Destination",
                        "parents": ["d2"],
                    }
                )
            return _Request(
                {
                    "driveId": "d1",
                    "id": "file",
                    "mimeType": "application/vnd.google-apps.document",
                    "name": "Document",
                    "parents": ["d1"],
                }
            )

        def update(self, **_: Any) -> _Request:
            operations.append("update")
            return _Request(
                {
                    "driveId": "d2",
                    "id": "file",
                    "mimeType": "application/vnd.google-apps.document",
                    "name": "Document",
                    "parents": ["destination"],
                }
            )

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    drive = _FakeDriveService(
        [
            DriveSummary(id=DriveId("d1"), name="Source"),
            DriveSummary(id=DriveId("d2"), name="Destination"),
        ]
    )
    service = GoogleFilesService(user=_user(), drive=drive)

    result = await service.move(FileId("file"), FileId("destination"))

    assert result.source.drive_id == "d1"
    assert result.moved.drive_id == "d2"
    assert set(operations[:2]) == {"get:file", "get:destination"}
    assert operations[-1] == "update"


async def test_known_unsupported_cross_drive_folder_move_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Request:
        def __init__(self, result: dict[str, Any] | None = None, *, fails: bool = False) -> None:
            self.result = result or {}
            self.fails = fails

        def execute(self) -> dict[str, Any]:
            if self.fails:
                response = Response({"reason": "bad request", "status": "403"})
                content = b'{"error":{"errors":[{"reason":"teamDrivesFolderMoveInNotSupported"}]}}'
                raise HttpError(response, content)
            return self.result

    class _FakeFiles:
        def get(self, **kwargs: Any) -> _Request:
            file_id = kwargs["fileId"]
            drive_id = "d2" if file_id == "destination" else "d1"
            return _Request(
                {
                    "capabilities": {"canAddChildren": file_id == "destination"},
                    "driveId": drive_id,
                    "id": file_id,
                    "mimeType": "application/vnd.google-apps.folder",
                    "name": file_id,
                    "parents": [drive_id],
                }
            )

        def update(self, **_: Any) -> _Request:
            return _Request(fails=True)

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    drive = _FakeDriveService(
        [DriveSummary(id=DriveId("d1"), name="Source"), DriveSummary(id=DriveId("d2"), name="Dest")]
    )
    service = GoogleFilesService(user=_user(), drive=drive)

    with pytest.raises(CrossDriveMoveNotSupportedError, match="cannot move this folder"):
        await service.move(FileId("folder"), FileId("destination"))


async def test_get_rejects_my_drive(monkeypatch: pytest.MonkeyPatch) -> None:

    class _FakeFiles:
        def get(self, **_: Any) -> Self:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "my-drive-file",
                "name": "Personal",
                "mimeType": "application/vnd.google-apps.document",
                "parents": [],
            }

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(FileNotInScopeError):
        await _svc("irrelevant").get(FileId("my-drive-file"))


async def test_html_content_rechecks_drive_and_downloads_with_viewer_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[str] = []

    class _Request:
        def __init__(self, result: Any) -> None:
            self._result = result

        def execute(self) -> Any:
            return self._result

    class _FakeFiles:
        def get(self, **kwargs: Any) -> _Request:
            operations.append("file:metadata")
            return _Request(
                {
                    "id": "chart",
                    "name": "Chart.html",
                    "mimeType": "text/html",
                    "parents": ["d1"],
                    "driveId": "d1",
                }
            )

        def get_media(self, **_: Any) -> _Request:
            operations.append("file:media")
            return _Request(b"<!doctype html><p>safe</p>")

    class _FakeDrives:
        def get(self, **kwargs: Any) -> _Request:
            operations.append(f"drive:{kwargs['driveId']}")
            return _Request({"id": "d1", "hidden": False})

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

        def drives(self) -> _FakeDrives:
            return _FakeDrives()

    _patch_client(monkeypatch, _FakeClient())
    content = await _svc("d1").get_html_content(FileId("chart"))

    assert content == b"<!doctype html><p>safe</p>"
    assert operations == ["file:metadata", "drive:d1", "file:media"]


async def test_personal_html_skips_shared_drive_membership_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations: list[str] = []

    class _PersonalDriveService(_FakeDriveService):
        async def resolve_member_drive(
            self,
            file_id: FileId,
            drive_id: DriveId | None,
            *,
            parent_id: FileId | None = None,
        ) -> DriveId | None:
            if file_id == "chart" and drive_id is None:
                return DriveId("personal-root")
            return await super().resolve_member_drive(file_id, drive_id, parent_id=parent_id)

    class _Request:
        def __init__(self, result: Any) -> None:
            self._result = result

        def execute(self) -> Any:
            return self._result

    class _FakeFiles:
        def get(self, **_: Any) -> _Request:
            operations.append("file:metadata")
            return _Request(
                {
                    "id": "chart",
                    "name": "Chart.html",
                    "mimeType": "text/html",
                    "parents": ["personal-root"],
                }
            )

        def get_media(self, **_: Any) -> _Request:
            operations.append("file:media")
            return _Request(b"<!doctype html><p>personal</p>")

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    drive = _PersonalDriveService(
        [DriveSummary(id=DriveId("personal-root"), name="My Drive", is_personal=True)]
    )
    service = GoogleFilesService(user=_user(), drive=drive)

    content = await service.get_html_content(FileId("chart"))

    assert content == b"<!doctype html><p>personal</p>"
    assert operations == ["file:metadata", "file:media"]


async def test_html_content_rejects_non_html_before_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> Self:
            return self

        def execute(self) -> dict[str, Any]:
            return {
                "id": "pdf",
                "name": "File.pdf",
                "mimeType": "application/pdf",
                "parents": ["d1"],
                "driveId": "d1",
            }

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(UnsupportedMimeError):
        await _svc("d1").get_html_content(FileId("pdf"))


async def test_copy_rejects_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeFiles:
        def get(self, **_: Any) -> Self:
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
        await _svc("d1").copy(FileId("folder-id"), "Copy of Some Folder")


async def test_copy_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, Any] = {}

    class _FakeFiles:
        def get(self, **_: Any) -> Self:
            self._mode = "get"
            return self

        def copy(self, **kwargs: Any) -> Self:
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
    result = await _svc("d1").copy(FileId("src-id"), "Copy of Architecture Notes")
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
        def get(self, **_: Any) -> Self:
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
    with pytest.raises(FileNotInScopeError):
        await _svc("other-drive").get(FileId("foreign-file"))
