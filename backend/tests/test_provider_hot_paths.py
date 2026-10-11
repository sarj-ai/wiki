from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, override
import uuid

from googleapiclient.errors import HttpError
from httplib2 import Response
from pydantic import JsonValue
import pytest

from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SLIDE, DriveSummary
from app.drive.service import GoogleDriveService
from app.files.errors import DriveUpstreamError
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from tests.support import dependency


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource


def _user() -> User:
    user = User()
    user.id = UserId(uuid.uuid7())
    user.google_id = GoogleUserId("round2-provider-fixture")
    user.email = "round2@example.com"
    user.token_version = 0
    return user


def _http_error(status: int) -> HttpError:
    return HttpError(Response({"status": str(status)}), b'{"error":"fixture"}')


class _ReadRequest:
    def __init__(self, response: JsonValue | HttpError) -> None:
        self.response = response

    def execute(self) -> JsonValue:
        if isinstance(self.response, HttpError):
            raise self.response
        return self.response


class _AncestorProvider:
    def __init__(self) -> None:
        self.locations: dict[FileId, JsonValue | HttpError] = {
            FileId("common-parent"): {"id": "common-parent", "parents": ["personal-root"]},
            FileId("outside-parent"): {"id": "outside-parent", "parents": []},
        }
        self.reads: Counter[FileId] = Counter()
        self.list_requests: list[dict[str, JsonValue]] = []
        self.pages: list[JsonValue] = []

    def files(self) -> Self:
        return self

    def get(self, **params: JsonValue) -> _ReadRequest:
        file_id = params["fileId"]
        assert isinstance(file_id, str)
        assert params["fields"] == "id,driveId,parents"
        assert params["supportsAllDrives"] is True
        self.reads[FileId(file_id)] += 1
        return _ReadRequest(self.locations[FileId(file_id)])

    def list(self, **params: JsonValue) -> _ReadRequest:
        self.list_requests.append(params)
        return _ReadRequest(self.pages.pop(0))


class _MembershipDrive(GoogleDriveService):
    def __init__(self, provider: _AncestorProvider) -> None:
        super().__init__(_user(), cache_service=None, refresh_handler=None)
        self.provider = provider
        self.drives = [
            DriveSummary(id=DriveId("personal-root"), name="My Drive", is_personal=True),
            DriveSummary(id=DriveId("shared"), name="Shared"),
        ]

    @override
    async def list_drives(self) -> list[DriveSummary]:
        return self.drives

    @override
    def _client(self) -> DriveResource:
        return dependency(self.provider)


@dataclass(frozen=True)
class _MetadataRequest:
    file_id: FileId
    fields: str


class _MetadataBatch:
    def __init__(
        self,
        provider: _MetadataProvider,
        callback: Callable[[str, JsonValue, BaseException | None], None],
    ) -> None:
        self.provider = provider
        self.callback = callback
        self.requests: list[tuple[str, _MetadataRequest]] = []

    def add(self, request: _MetadataRequest, *, request_id: str) -> None:
        self.requests.append((request_id, request))

    def execute(self) -> None:
        self.provider.batches += 1
        for request_id, request in self.requests:
            response = self.provider.records[request.file_id]
            if isinstance(response, HttpError):
                self.callback(request_id, None, response)
            else:
                self.callback(request_id, response, None)


class _MetadataProvider:
    def __init__(self, records: dict[FileId, JsonValue | HttpError]) -> None:
        self.records = records
        self.requests: list[_MetadataRequest] = []
        self.batches = 0

    def files(self) -> Self:
        return self

    def get(self, **params: JsonValue) -> _MetadataRequest:
        file_id = params["fileId"]
        fields = params["fields"]
        assert isinstance(file_id, str)
        assert isinstance(fields, str)
        assert params["supportsAllDrives"] is True
        request = _MetadataRequest(FileId(file_id), fields)
        self.requests.append(request)
        return request

    def new_batch_http_request(
        self, *, callback: Callable[[str, JsonValue, BaseException | None], None]
    ) -> _MetadataBatch:
        return _MetadataBatch(self, callback)


class _MetadataFiles(GoogleFilesService):
    def __init__(self, drive: _MembershipDrive, provider: _MetadataProvider) -> None:
        super().__init__(user=_user(), drive=drive)
        self.provider = provider

    @override
    def _client(self) -> DriveResource:
        return dependency(self.provider)


@pytest.mark.parametrize("strict", [False, True], ids=["normal", "bounded"])
async def test_bulk_membership_checks_common_parent_once_and_remains_fresh(strict: bool) -> None:
    provider = _AncestorProvider()
    drive = _MembershipDrive(provider)
    files: dict[FileId, DriveId | None] = {FileId(f"personal-{i}"): None for i in range(200)}
    parents = {file_id: FileId("common-parent") for file_id in files}
    files.update(
        {
            FileId("personal-root"): None,
            FileId("direct"): None,
            FileId("shared-file"): DriveId("shared"),
            FileId("orphan"): None,
            FileId("outside"): None,
            FileId("foreign"): DriveId("foreign"),
        }
    )
    parents.update(
        {FileId("direct"): FileId("personal-root"), FileId("outside"): FileId("outside-parent")}
    )
    token = drive._bounded_drives.set(drive.drives if strict else None)
    try:
        first = await drive.resolve_member_drives(files, parent_ids=parents)
        assert len(first) == 203
        assert first[FileId("personal-root")] == "personal-root"
        assert FileId("orphan") not in first
        assert FileId("outside") not in first
        assert FileId("foreign") not in first
        assert provider.reads == {FileId("common-parent"): 1, FileId("outside-parent"): 1}

        provider.locations[FileId("common-parent")] = _http_error(403)
        second = await drive.resolve_member_drives(files, parent_ids=parents)
        assert set(second) == {FileId("personal-root"), FileId("direct"), FileId("shared-file")}
        assert provider.reads[FileId("common-parent")] == 2
    finally:
        drive._bounded_drives.reset(token)


async def test_access_only_bulk_read_projects_scope_and_filters_bad_or_denied_records() -> None:
    ancestors = _AncestorProvider()
    drive = _MembershipDrive(ancestors)
    provider = _MetadataProvider(
        {
            FileId("personal"): {"id": "personal", "parents": ["common-parent"]},
            FileId("shared"): {"id": "shared", "driveId": "shared"},
            FileId("denied"): _http_error(403),
            FileId("missing"): _http_error(404),
            FileId("trashed"): {"id": "trashed", "driveId": "shared", "trashed": True},
            FileId("mismatched"): {"id": "other-file", "driveId": "shared"},
            FileId("outside"): {"id": "outside", "parents": ["outside-parent"]},
        }
    )
    files = _MetadataFiles(drive, provider)

    assert await files.get_accessible_ids([*provider.records, FileId("personal")]) == {
        FileId("personal"),
        FileId("shared"),
    }
    assert provider.batches == 1
    assert len(provider.requests) == 7
    assert all(request.fields == "id,parents,driveId,trashed" for request in provider.requests)
    assert ancestors.reads == {FileId("common-parent"): 1, FileId("outside-parent"): 1}

    provider.records[FileId("shared")] = _http_error(403)
    assert await files.get_accessible_ids([FileId("shared")]) == set()
    assert provider.batches == 2


async def test_search_hydration_keeps_fresh_metadata_and_omits_unused_fields() -> None:
    drive = _MembershipDrive(_AncestorProvider())
    provider = _MetadataProvider(
        {
            FileId("document"): {
                "id": "document",
                "name": "Fresh title",
                "mimeType": "text/plain",
                "driveId": "shared",
                "modifiedTime": "2026-10-10T00:00:00Z",
            }
        }
    )
    files = _MetadataFiles(drive, provider)
    first = await files.get_search_metadata_many([FileId("document")])
    assert first[FileId("document")].name == "Fresh title"
    assert provider.requests[0].fields == "id,name,mimeType,parents,driveId,modifiedTime,trashed"
    provider.records[FileId("document")] = _http_error(404)
    assert await files.get_search_metadata_many([FileId("document")]) == {}
    assert provider.batches == 2


async def test_access_bulk_read_propagates_upstream_failure() -> None:
    drive = _MembershipDrive(_AncestorProvider())
    provider = _MetadataProvider({FileId("failed"): _http_error(500)})
    with pytest.raises(DriveUpstreamError):
        await _MetadataFiles(drive, provider).get_accessible_ids([FileId("failed")])


async def test_access_bulk_read_empty_input_does_not_construct_provider() -> None:
    drive = _MembershipDrive(_AncestorProvider())
    provider = _MetadataProvider({})
    assert await _MetadataFiles(drive, provider).get_accessible_ids([]) == set()
    assert provider.batches == 0
    assert provider.requests == []


async def test_template_child_read_filters_and_projects_without_losing_pagination() -> None:
    provider = _AncestorProvider()
    provider.pages = [
        {
            "nextPageToken": "second-page",
            "files": [
                {"id": "category", "name": "Planning", "mimeType": MIME_FOLDER},
                {
                    "id": "document",
                    "name": "PRD",
                    "mimeType": MIME_DOC,
                    "description": "Product requirements",
                    "modifiedTime": "2026-10-10T00:00:00Z",
                },
            ],
        },
        {"files": [{"id": "slides", "name": "Presentation", "mimeType": MIME_SLIDE}]},
    ]
    nodes = await _MembershipDrive(provider).list_template_children(FileId("templates"))

    assert [node.id for node in nodes] == ["category", "document", "slides"]
    assert all(node.parent_id == "templates" for node in nodes)
    assert nodes[1].description == "Product requirements"
    assert nodes[1].modified_time == "2026-10-10T00:00:00Z"
    assert len(provider.list_requests) == 2
    first, second = provider.list_requests
    assert first["fields"] == "nextPageToken,files(id,name,mimeType,modifiedTime,description)"
    assert first["q"] == (
        "'templates' in parents and trashed=false and "
        f"(mimeType='{MIME_FOLDER}' or mimeType='{MIME_DOC}' or mimeType='{MIME_SLIDE}')"
    )
    assert second["pageToken"] == "second-page"
    assert provider.reads == {}
