from __future__ import annotations

from typing import Any, Self
import uuid

from googleapiclient.errors import HttpError
from httplib2 import Response
import pytest

from app.drive.schemas import DriveNode, DriveSummary, DriveTree, SearchResult, SnippetResult
from app.drive.service import DriveService, GoogleDriveService
from app.files.errors import (
    CrossDriveMoveNotSupportedError,
    DriveUpstreamError,
    FileNotInScopeError,
    FileVersionChangedError,
    FolderCopyNotSupportedError,
    UnsupportedMimeError,
)
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, GoogleUserId
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


def _user() -> User:
    u = User()
    u.id = uuid.uuid4()
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
    response = Response({"reason": "test", "status": str(status_code)})
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
    file_id: str,
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
            self, file_id: FileId, drive_id: DriveId | None
        ) -> DriveId | None:
            if file_id == FileId("recovered") and drive_id is None:
                return DriveId("personal-root")
            return await super().resolve_member_drive(file_id, drive_id)

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

    result = await service.find_by_task(DriveId("personal-root"), "task-id")

    assert result is not None
    assert result.drive_id == DriveId("personal-root")
    assert list_params[0]["corpora"] == "user"
    assert "driveId" not in list_params[0]


async def test_move_reuses_prefetched_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    ops: list[dict[str, Any]] = []

    class _FakeFiles:
        def __init__(self) -> None:
            self._result: dict[str, Any] = {}

        def get(self, **kwargs: Any) -> Self:
            ops.append({"_op": "get", **kwargs})
            self._result = {
                "id": "f",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["old-parent"],
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
    assert len(gets) == 1
    assert len(updates) == 1
    assert updates[0]["removeParents"] == "old-parent"
    assert updates[0]["addParents"] == "new-parent"


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
            self, file_id: FileId, drive_id: DriveId | None
        ) -> DriveId | None:
            if file_id == "chart" and drive_id is None:
                return DriveId("personal-root")
            return await super().resolve_member_drive(file_id, drive_id)

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


def _file_meta(
    file_id: str,
    *,
    mime: str = "application/vnd.google-apps.document",
    drive_id: str = "d1",
    modified: str = "2026-08-01T00:00:00Z",
    can_add_children: bool = True,
) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": file_id,
        "mimeType": mime,
        "parents": ["root"],
        "driveId": drive_id,
        "modifiedTime": modified,
        "capabilities": {"canEdit": True, "canAddChildren": can_add_children},
    }


class _RoutedRequest:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _RoutedClient:
    """files() client that answers get() per fileId and records mutations."""

    def __init__(
        self,
        metas: dict[str, dict[str, Any]],
        *,
        update_error: Exception | None = None,
    ) -> None:
        self._metas = metas
        self._update_error = update_error
        self.copies: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    def files(self) -> Self:
        return self

    def get(self, **kwargs: Any) -> _RoutedRequest:
        return _RoutedRequest(self._metas[kwargs["fileId"]])

    def copy(self, **kwargs: Any) -> _RoutedRequest:
        self.copies.append(kwargs)
        return _RoutedRequest(_file_meta("copy-result"))

    def update(self, **kwargs: Any) -> _RoutedRequest:
        self.updates.append(kwargs)
        if self._update_error is not None:
            return _RoutedRequest(self._update_error)
        return _RoutedRequest(self._metas[kwargs["fileId"]])


def _two_drive_service(
    client: _RoutedClient, monkeypatch: pytest.MonkeyPatch
) -> GoogleFilesService:
    _patch_client(monkeypatch, client)
    drives = [
        DriveSummary(id=DriveId("d1"), name="Drive One"),
        DriveSummary(id=DriveId("d2"), name="Drive Two"),
    ]
    return GoogleFilesService(user=_user(), drive=_FakeDriveService(drives))


async def test_copy_template_rejects_folder_source(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _RoutedClient(
        {
            "tpl": _file_meta("tpl", mime="application/vnd.google-apps.folder"),
            "dest": _file_meta("dest", mime="application/vnd.google-apps.folder"),
        }
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(FolderCopyNotSupportedError):
        await _svc("d1").copy_template(
            template_file_id=FileId("tpl"),
            parent_folder_id=FileId("dest"),
            name="Copy",
            task_id="t1",
            expected_modified_time="2026-08-01T00:00:00Z",
        )


async def test_copy_template_rejects_stale_template_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient(
        {
            "tpl": _file_meta("tpl", modified="2026-08-02T09:00:00Z"),
            "dest": _file_meta("dest", mime="application/vnd.google-apps.folder"),
        }
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(FileVersionChangedError):
        await _svc("d1").copy_template(
            template_file_id=FileId("tpl"),
            parent_folder_id=FileId("dest"),
            name="Copy",
            task_id="t1",
            expected_modified_time="2026-08-01T00:00:00Z",  # reviewed version is older
        )

    assert client.copies == []


async def test_copy_template_rejects_readonly_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient(
        {
            "tpl": _file_meta("tpl"),
            "dest": _file_meta(
                "dest", mime="application/vnd.google-apps.folder", can_add_children=False
            ),
        }
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(FileNotInScopeError):
        await _svc("d1").copy_template(
            template_file_id=FileId("tpl"),
            parent_folder_id=FileId("dest"),
            name="Copy",
            task_id="t1",
            expected_modified_time="2026-08-01T00:00:00Z",
        )


async def test_copy_template_rejects_cross_drive_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient(
        {
            "tpl": _file_meta("tpl", drive_id="d1"),
            "dest": _file_meta("dest", mime="application/vnd.google-apps.folder", drive_id="d2"),
        }
    )
    service = _two_drive_service(client, monkeypatch)

    with pytest.raises(FileNotInScopeError):
        await service.copy_template(
            template_file_id=FileId("tpl"),
            parent_folder_id=FileId("dest"),
            name="Copy",
            task_id="t1",
            expected_modified_time="2026-08-01T00:00:00Z",
        )


async def test_copy_template_tags_the_copy_with_the_task_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient(
        {
            "tpl": _file_meta("tpl"),
            "dest": _file_meta("dest", mime="application/vnd.google-apps.folder"),
        }
    )
    _patch_client(monkeypatch, client)

    result = await _svc("d1").copy_template(
        template_file_id=FileId("tpl"),
        parent_folder_id=FileId("dest"),
        name="Copy of Template",
        task_id="task-9",
        expected_modified_time="2026-08-01T00:00:00Z",
    )

    assert result.id == "copy-result"
    assert len(client.copies) == 1
    body = client.copies[0]["body"]
    assert body["appProperties"] == {"sarjWikiTaskId": "task-9"}
    assert body["parents"] == ["dest"]


async def test_create_tracked_rejects_non_folder_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient({"dest": _file_meta("dest")})  # a document, not a folder
    _patch_client(monkeypatch, client)

    with pytest.raises(FileNotInScopeError):
        await _svc("d1").create_tracked(
            FileId("dest"),
            "Doc",
            "application/vnd.google-apps.document",
            "task-1",
        )


async def test_move_403_to_another_drive_maps_to_cross_drive_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient(
        {
            "f": _file_meta("f", drive_id="d1"),
            "new-parent": _file_meta(
                "new-parent", mime="application/vnd.google-apps.folder", drive_id="d2"
            ),
        },
        update_error=_make_http_error(403),
    )
    service = _two_drive_service(client, monkeypatch)

    with pytest.raises(CrossDriveMoveNotSupportedError):
        await service.move(FileId("f"), FileId("new-parent"))


async def test_move_403_within_the_same_drive_reraises_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient(
        {
            "f": _file_meta("f"),
            "new-parent": _file_meta("new-parent", mime="application/vnd.google-apps.folder"),
        },
        update_error=_make_http_error(403),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(DriveUpstreamError) as excinfo:
        await _svc("d1").move(FileId("f"), FileId("new-parent"))
    assert excinfo.value.http_status == 403


async def test_move_403_to_out_of_scope_destination_reraises_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient(
        {
            "f": _file_meta("f"),
            "new-parent": _file_meta(
                "new-parent", mime="application/vnd.google-apps.folder", drive_id="foreign"
            ),
        },
        update_error=_make_http_error(403),
    )
    _patch_client(monkeypatch, client)

    with pytest.raises(DriveUpstreamError) as excinfo:
        await _svc("d1").move(FileId("f"), FileId("new-parent"))
    assert excinfo.value.http_status == 403


async def test_delete_trashes_the_file_and_returns_its_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RoutedClient({"doomed": _file_meta("doomed")})
    _patch_client(monkeypatch, client)

    result = await _svc("d1").delete(FileId("doomed"))

    assert result.source.id == "doomed"
    assert len(client.updates) == 1
    assert client.updates[0]["body"] == {"trashed": True}


async def test_html_content_rejects_oversized_files(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Request:
        def __init__(self, result: Any) -> None:
            self._result = result

        def execute(self) -> Any:
            return self._result

    class _FakeFiles:
        def get(self, **_: Any) -> _Request:
            return _Request(_file_meta("big", mime="text/html"))

        def get_media(self, **_: Any) -> _Request:
            return _Request(b"x" * (5 * 1024 * 1024 + 1))

    class _FakeDrives:
        def get(self, **_: Any) -> _Request:
            return _Request({"id": "d1", "hidden": False})

    class _FakeClient:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

        def drives(self) -> _FakeDrives:
            return _FakeDrives()

    _patch_client(monkeypatch, _FakeClient())
    with pytest.raises(UnsupportedMimeError):
        await _svc("d1").get_html_content(FileId("big"))
