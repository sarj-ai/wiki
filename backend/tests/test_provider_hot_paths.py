from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING, Literal, Self, override
import uuid

from googleapiclient.errors import HttpError
from httplib2 import Response
from pydantic import JsonValue
import pytest

from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SHORTCUT, MIME_SLIDE, DriveSummary
from app.drive.service import GoogleDriveService
from app.files.errors import DriveUpstreamError
from app.files.schemas import FileResponse
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


class _ConcurrentRead(_ReadRequest):
    def __init__(
        self,
        response: JsonValue | HttpError,
        *,
        file_id: FileId,
        parents_ready: threading.Barrier,
    ) -> None:
        super().__init__(response)
        self.file_id = file_id
        self.parents_ready = parents_ready

    @override
    def execute(self) -> JsonValue:
        if self.file_id in {"left", "right"}:
            self.parents_ready.wait(timeout=3)
        return super().execute()


class _ConcurrentAncestorProvider(_AncestorProvider):
    def __init__(self) -> None:
        super().__init__()
        self.locations = {
            FileId("left"): {"id": "left", "parents": ["common"]},
            FileId("right"): {"id": "right", "parents": ["common"]},
            FileId("common"): {"id": "common", "parents": ["personal-root"]},
        }
        self.parents_ready = threading.Barrier(2)

    @override
    def get(self, **params: JsonValue) -> _ReadRequest:
        original = super().get(**params)
        file_id = params["fileId"]
        assert isinstance(file_id, str)
        return _ConcurrentRead(
            original.response,
            file_id=FileId(file_id),
            parents_ready=self.parents_ready,
        )


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


@pytest.mark.parametrize("strict", [False, True], ids=["normal", "bounded"])
async def test_distinct_parents_reuse_common_ancestors_only_within_current_call(
    strict: bool,
) -> None:
    provider = _AncestorProvider()
    provider.locations = {
        **{
            FileId(f"parent-{i}"): {"id": f"parent-{i}", "parents": ["common-parent"]}
            for i in range(200)
        },
        FileId("common-parent"): {"id": "common-parent", "parents": ["personal-root"]},
    }
    drive = _MembershipDrive(provider)
    files: dict[FileId, DriveId | None] = {FileId(f"file-{i}"): None for i in range(200)}
    files[FileId("shared-file")] = DriveId("shared")
    parents = {FileId(f"file-{i}"): FileId(f"parent-{i}") for i in range(200)}
    token = drive._bounded_drives.set(drive.drives if strict else None)
    try:
        assert len(await drive.resolve_member_drives(files, parent_ids=parents)) == 201
        assert sum(provider.reads.values()) == 201
        assert provider.reads[FileId("common-parent")] == 1

        provider.locations[FileId("common-parent")] = _http_error(403)
        assert await drive.resolve_member_drives(files, parent_ids=parents) == {
            FileId("shared-file"): DriveId("shared")
        }
        assert sum(provider.reads.values()) == 402
        assert provider.reads[FileId("common-parent")] == 2
    finally:
        drive._bounded_drives.reset(token)


async def test_ancestor_memo_keeps_independent_parent_provider_reads_concurrent() -> None:
    provider = _ConcurrentAncestorProvider()
    drive = _MembershipDrive(provider)
    files: dict[FileId, DriveId | None] = {
        FileId("left-child"): None,
        FileId("right-child"): None,
    }
    parents = {FileId("left-child"): FileId("left"), FileId("right-child"): FileId("right")}

    assert len(await drive.resolve_member_drives(files, parent_ids=parents)) == 2
    assert provider.reads == {FileId("left"): 1, FileId("right"): 1, FileId("common"): 1}


@pytest.mark.parametrize("strict", [False, True], ids=["normal", "bounded"])
@pytest.mark.parametrize("status", [403, 404, 418])
async def test_ancestor_memo_preserves_denials_partial_results_and_provider_errors(
    strict: bool,
    status: int,
) -> None:
    provider = _AncestorProvider()
    provider.locations = {
        FileId("left"): {"id": "left", "parents": ["common-parent"]},
        FileId("right"): {"id": "right", "parents": ["common-parent"]},
        FileId("common-parent"): _http_error(status),
    }
    drive = _MembershipDrive(provider)
    files: dict[FileId, DriveId | None] = {
        FileId("left-child"): None,
        FileId("right-child"): None,
        FileId("shared-file"): DriveId("shared"),
    }
    parents = {FileId("left-child"): FileId("left"), FileId("right-child"): FileId("right")}
    token = drive._bounded_drives.set(drive.drives if strict else None)
    try:
        if strict and status == 418:
            with pytest.raises(HttpError) as caught:
                await drive.resolve_member_drives(files, parent_ids=parents)
            assert caught.value.resp.status == status
        else:
            assert await drive.resolve_member_drives(files, parent_ids=parents) == {
                FileId("shared-file"): DriveId("shared")
            }
        assert provider.reads[FileId("common-parent")] == 1
    finally:
        drive._bounded_drives.reset(token)


async def test_strict_ancestor_memo_rejects_mismatched_response_identity() -> None:
    provider = _AncestorProvider()
    provider.locations = {
        FileId("left"): {"id": "left", "parents": ["common-parent"]},
        FileId("right"): {"id": "right", "parents": ["common-parent"]},
        FileId("common-parent"): {"id": "different", "parents": ["personal-root"]},
    }
    drive = _MembershipDrive(provider)
    files: dict[FileId, DriveId | None] = {
        FileId("left-child"): None,
        FileId("right-child"): None,
    }
    parents = {FileId("left-child"): FileId("left"), FileId("right-child"): FileId("right")}
    token = drive._bounded_drives.set(drive.drives)
    try:
        with pytest.raises(ValueError, match="Invalid personal ancestry response"):
            await drive.resolve_member_drives(files, parent_ids=parents)
        assert provider.reads[FileId("common-parent")] == 1
    finally:
        drive._bounded_drives.reset(token)


@pytest.mark.parametrize("cycle", [False, True], ids=["depth-cap", "cycle"])
async def test_ancestor_reuse_does_not_bypass_walk_limits_or_cycles(cycle: bool) -> None:
    provider = _AncestorProvider()
    provider.locations = {
        FileId("left"): {"id": "left", "parents": ["chain-0"]},
        FileId("right"): {"id": "right", "parents": ["chain-0"]},
        **{
            FileId(f"chain-{i}"): {"id": f"chain-{i}", "parents": [f"chain-{i + 1}"]}
            for i in range(70)
        },
    }
    provider.locations[FileId("chain-69")] = {"id": "chain-69", "parents": ["personal-root"]}
    if cycle:
        provider.locations[FileId("chain-0")] = {"id": "chain-0", "parents": ["left"]}
    drive = _MembershipDrive(provider)
    files: dict[FileId, DriveId | None] = {
        FileId("left-child"): None,
        FileId("right-child"): None,
    }
    parents = {FileId("left-child"): FileId("left"), FileId("right-child"): FileId("right")}
    token = drive._bounded_drives.set(drive.drives)
    try:
        assert await drive.resolve_member_drives(files, parent_ids=parents) == {}
        assert sum(provider.reads.values()) == (3 if cycle else 65)
        assert FileId("chain-63") not in provider.reads
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


@pytest.mark.parametrize("name", ["Templates", "Owner's \\ Templates"])
async def test_named_template_roots_keep_all_pages_and_filter_name_and_mime(name: str) -> None:
    provider = _AncestorProvider()
    provider.pages = [
        {
            "nextPageToken": "second-page",
            "files": [
                {"id": "first", "name": name, "mimeType": MIME_FOLDER},
                {"id": "wrong-case", "name": name.lower(), "mimeType": MIME_FOLDER},
                {"id": "wrong-mime", "name": name, "mimeType": MIME_DOC},
            ],
        },
        {"files": [{"id": "second", "name": name, "mimeType": MIME_FOLDER}]},
    ]

    nodes = await _MembershipDrive(provider).list_named_root_folders(DriveId("shared"), name=name)

    assert [node.id for node in nodes] == ["first", "second"]
    assert all(node.parent_id == "shared" for node in nodes)
    first, second = provider.list_requests
    assert first["fields"] == "nextPageToken,files(id,name,mimeType)"
    escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
    assert first["q"] == (
        f"'shared' in parents and trashed=false and (mimeType='{MIME_FOLDER}') "
        f"and name='{escaped_name}'"
    )
    assert second["q"] == first["q"]
    assert second["pageToken"] == "second-page"
    assert provider.reads == {}


type _MetadataReadKind = Literal["full", "search", "qa", "access"]


async def _read_metadata(
    files: _MetadataFiles,
    file_ids: list[FileId],
    kind: _MetadataReadKind,
) -> dict[FileId, FileResponse] | set[FileId]:
    match kind:
        case "search":
            return await files.get_search_metadata_many(file_ids)
        case "qa":
            return await files.get_qa_corpus_metadata_many(file_ids)
        case "access":
            return await files.get_accessible_ids(file_ids)
        case _:
            return await files.get_accessible_many(file_ids)


@pytest.mark.parametrize("kind", ["full", "search", "qa", "access"])
async def test_bulk_metadata_binds_response_identity_before_membership(
    kind: _MetadataReadKind,
) -> None:
    ancestors = _AncestorProvider()
    provider = _MetadataProvider(
        {
            FileId("requested"): {
                "id": "different",
                "name": "Unrequested file",
                "mimeType": MIME_DOC,
                "parents": ["common-parent"],
            },
            FileId("different"): _http_error(403),
            FileId("valid"): {
                "id": "valid",
                "name": "Valid file",
                "mimeType": MIME_DOC,
                "driveId": "shared",
            },
            FileId("shortcut"): {
                "id": "shortcut",
                "name": "Shortcut",
                "mimeType": MIME_SHORTCUT,
                "driveId": "shared",
                "shortcutDetails": {"targetId": "different", "targetMimeType": MIME_DOC},
            },
            FileId("missing"): _http_error(404),
            FileId("trashed"): {
                "id": "trashed",
                "name": "Trashed file",
                "mimeType": MIME_DOC,
                "driveId": "shared",
                "trashed": True,
            },
        }
    )
    files = _MetadataFiles(_MembershipDrive(ancestors), provider)

    result = await _read_metadata(files, [*provider.records, FileId("valid")], kind)

    if isinstance(result, dict):
        assert list(result) == [FileId("valid"), FileId("shortcut")]
    else:
        assert result == {FileId("valid"), FileId("shortcut")}
    assert provider.batches == 1
    assert [request.file_id for request in provider.requests] == list(provider.records)
    assert ancestors.reads == {}


@pytest.mark.parametrize("kind", ["full", "search", "qa", "access"])
async def test_bulk_metadata_identity_filter_preserves_upstream_error(
    kind: _MetadataReadKind,
) -> None:
    provider = _MetadataProvider(
        {
            FileId("requested"): {
                "id": "different",
                "name": "Unrequested file",
                "mimeType": MIME_DOC,
                "driveId": "shared",
            },
            FileId("failed"): _http_error(500),
        }
    )
    files = _MetadataFiles(_MembershipDrive(_AncestorProvider()), provider)

    with pytest.raises(DriveUpstreamError) as caught:
        await _read_metadata(files, list(provider.records), kind)

    assert caught.value.http_status == 502
    assert provider.batches == 1
