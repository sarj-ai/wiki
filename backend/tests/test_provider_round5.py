from __future__ import annotations

from collections.abc import Sequence
import json
from typing import TYPE_CHECKING, Self, override

from googleapiclient.errors import HttpError
from httplib2 import Response
from pydantic import JsonValue, ValidationError
import pytest

from app.drive.errors import DriveTraversalIncompleteError
from app.drive.google_schemas import GoogleFile
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.drive.service import GoogleDriveService
from app.files.errors import DriveUpstreamError
from app.identifiers import DriveId, FileId
from tests.support import dependency
from tests.test_drive_modules import _FakeClient
from tests.test_provider_hot_paths import _MetadataFiles, _MetadataProvider, _user
from tests.test_provider_round3 import _CountingDrive, _SingleFiles, _SingleProvider, _SingleRequest
from tests.test_provider_round4 import _SearchDrive, _SearchTransport


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource


def test_inventory_parent_tuples_validate_all_ids_and_preserve_json_arrays() -> None:
    first = GoogleFile.model_validate({"id": "a", "name": "A", "mimeType": MIME_DOC})
    second = GoogleFile.model_validate({"id": "b", "name": "B", "mimeType": MIME_DOC})
    assert first.parents == second.parents == ()
    assert first.parents is second.parents
    first.parents = (FileId("folder"),)
    assert second.parents == ()
    assert first.model_dump(mode="json")["parents"] == ["folder"]
    with pytest.raises(ValidationError):
        GoogleFile.model_validate(
            {"id": "bad", "name": "Bad", "mimeType": MIME_DOC, "parents": ["valid", {}]}
        )


class _FolderProvider:
    def __init__(self, pages: Sequence[dict[str, JsonValue]]) -> None:
        self.pages = pages
        self.page = 0
        self.child_calls: list[dict[str, JsonValue]] = []

    def files(self) -> Self:
        return self

    def drives(self) -> Self:
        return self

    def get(self, **params: JsonValue) -> _SingleRequest:
        assert params["fileId"] == "root"
        return _SingleRequest({"id": "personal"})

    def list(self, **params: JsonValue) -> _SingleRequest:
        if "corpora" not in params:
            return _SingleRequest({"drives": [{"id": "shared", "name": "Shared"}]})
        self.child_calls.append(params)
        response = self.pages[self.page]
        self.page += 1
        return _SingleRequest(response)


class _FolderDrive(GoogleDriveService):
    def __init__(self, provider: _FolderProvider) -> None:
        super().__init__(_user())
        self.provider = provider

    @override
    def _client(self) -> DriveResource:
        client: DriveResource = dependency(self.provider)
        return client


async def test_member_metadata_budget_is_exact_and_independent_of_key_order() -> None:
    raw: dict[str, JsonValue] = {
        "version": "7",
        "parents": ["folder"],
        "name": "Planning 日本語 😀",
        "mimeType": MIME_DOC,
        "id": "document",
        "driveId": "shared",
        "modifiedTime": "2026-10-11T00:00:00Z",
    }
    previous = {**raw, "id": "previous", "name": "Previous 日本語"}
    counted = sum(
        len(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())
        for row in (previous, raw)
    )
    for ordered in (raw, dict(reversed(tuple(raw.items())))):
        pages: list[dict[str, JsonValue]] = [
            {"files": [previous], "nextPageToken": "next"},
            {"files": [ordered]},
        ]
        provider = _FolderProvider(pages)
        result = await _FolderDrive(provider).list_folder_members_bounded(
            FileId("folder"),
            drive_id=DriveId("shared"),
            max_pages=2,
            max_children=2,
            max_metadata_bytes=counted,
        )
        assert result.metadata_bytes == counted
        assert result.page_count == 2
        assert [member.id for member in result.members] == ["previous", "document"]
        assert result.members[1].parent_id == "folder"
        assert provider.child_calls[1]["pageToken"] == "next"
        with pytest.raises(DriveTraversalIncompleteError):
            await _FolderDrive(_FolderProvider(pages)).list_folder_members_bounded(
                FileId("folder"),
                drive_id=DriveId("shared"),
                max_pages=2,
                max_children=2,
                max_metadata_bytes=counted - 1,
            )


@pytest.mark.parametrize("parents", [["other"], ["folder", "other"], []])
async def test_tuple_membership_still_rejects_nonexact_folder_parent(
    parents: list[JsonValue],
) -> None:
    provider = _FolderProvider(
        [
            {
                "files": [
                    {"id": "document", "name": "Document", "mimeType": MIME_DOC, "parents": parents}
                ]
            }
        ]
    )
    with pytest.raises(DriveTraversalIncompleteError):
        await _FolderDrive(provider).list_folder_members_bounded(
            FileId("folder"),
            drive_id=DriveId("personal"),
            max_pages=1,
            max_children=1,
            max_metadata_bytes=1024,
        )
    assert provider.child_calls[0]["corpora"] == "user"
    assert provider.child_calls[0]["supportsAllDrives"] is True


async def test_bounded_search_publishes_final_names_with_fresh_owned_scope() -> None:
    transport = _SearchTransport(shared_drives=2)
    drive = _SearchDrive(transport)
    async with drive.search_bounded("document") as result:
        assert [(hit.id, hit.drive_name) for hit in result.files] == [
            ("owned", "My Drive"),
            ("document-0", "Drive 0"),
            ("document-1", "Drive 1"),
        ]
        assert result.truncated
    assert len(transport.calls) == 3
    assert "'me' in owners" in transport.calls[0]["q"]


def _reference_text(text: str | None) -> str | None:
    if text is None:
        return None
    collapsed = " ".join(text.split())
    if not collapsed:
        return None
    if len(collapsed) <= 12_000:
        return collapsed
    return f"{collapsed[:8_000]} … [middle omitted] … {collapsed[-4_000:]}"


class _TextDrive(GoogleDriveService):
    def __init__(self, text: str | None) -> None:
        super().__init__(_user())
        self.text = text
        self.exports = 0

    @override
    def _export_text_sync(self, file_id: FileId) -> str | None:
        assert file_id == "document"
        self.exports += 1
        return self.text


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        " \t\n\u2003 " * 60_000,
        " \u001c\u0085\u2003A\tB\nC\u3000 ",
        "😀" * 12_000,
        "😀" * 12_001,
        "plan \n\u2003 document " * 100_000,
        " " * 100_000 + "head " * 6_000,
        "head " * 6_000 + " " * 100_000,
        "head " * 3_000 + " " * 200_000 + "tail " * 3_000,
        "a" + " " * 100_000 + "b",
        "a" * 8_000 + "😀" + "b" * 8_000 + "😀" * 4_000,
    ],
    ids=[
        "unavailable",
        "empty",
        "only-whitespace",
        "unicode-whitespace",
        "exact-codepoint-limit",
        "above-codepoint-limit",
        "large-wordy-document",
        "leading-whitespace",
        "trailing-whitespace",
        "large-middle-whitespace",
        "short-after-collapse",
        "head-and-tail-codepoints",
    ],
)
async def test_windowed_document_text_matches_full_collapse(text: str | None) -> None:
    drive = _TextDrive(text)
    assert await drive.fetch_document_text(FileId("document")) == _reference_text(text)
    assert drive.exports == 1


class _ExportProvider:
    def __init__(self, response: bytes | HttpError) -> None:
        self.response = response
        self.exports = 0

    def files(self) -> Self:
        return self

    def export(self, **params: str) -> Self:
        assert params == {"fileId": "document", "mimeType": "text/plain"}
        self.exports += 1
        return self

    def execute(self) -> bytes:
        if isinstance(self.response, HttpError):
            raise self.response
        return self.response


class _ExportDrive(GoogleDriveService):
    def __init__(self, provider: _ExportProvider) -> None:
        super().__init__(_user())
        self.provider = provider

    @override
    def _client(self) -> DriveResource:
        client: DriveResource = dependency(self.provider)
        return client


async def test_windowed_text_keeps_existing_export_byte_and_utf8_boundary() -> None:
    raw = b"a" * (2 * 1024 * 1024 - 1) + "😀tail".encode()
    provider = _ExportProvider(raw)
    expected = _reference_text(raw[: 2 * 1024 * 1024].decode("utf-8", errors="replace"))
    actual = await _ExportDrive(provider).fetch_document_text(FileId("document"))
    assert actual == expected
    assert actual is not None
    assert actual.endswith("�")
    assert provider.exports == 1


@pytest.mark.parametrize("status", [403, 404])
async def test_windowed_text_keeps_unavailable_export_classification(status: int) -> None:
    provider = _ExportProvider(HttpError(Response({"status": str(status)}), b"{}"))
    assert await _ExportDrive(provider).fetch_document_text(FileId("document")) is None
    assert provider.exports == 1


async def test_failed_metadata_batch_keeps_first_error_and_all_fresh_subrequests() -> None:
    drive = _CountingDrive()
    provider = _MetadataProvider(
        {
            FileId(f"file-{index}"): HttpError(
                Response({"status": "500"}),
                json.dumps({"error": {"errors": [{"reason": f"failure-{index}"}]}}).encode(),
            )
            for index in range(100)
        }
    )
    with pytest.raises(DriveUpstreamError) as raised:
        await _MetadataFiles(drive, provider).get_accessible_many(list(provider.records))
    assert raised.value.http_status == 502
    assert raised.value.reason == "failure-0"
    assert len(provider.requests) == 100
    assert provider.batches == 1
    assert drive.inventory_reads == 0


async def test_six_recent_metadata_candidates_remain_fresh_and_member_normalized() -> None:
    provider = _MetadataProvider(
        {
            FileId(f"file-{index}"): {
                "id": f"file-{index}",
                "name": f"Document {index}",
                "mimeType": MIME_DOC,
                "parents": ["personal-root"] if index < 3 else ["shared"],
                **({} if index < 3 else {"driveId": "shared"}),
            }
            for index in range(6)
        }
    )
    ids = list(provider.records)
    service = _MetadataFiles(_CountingDrive(), provider)
    initial = await service.get_search_metadata_many(ids)
    assert list(initial) == ids
    assert initial[ids[0]].drive_id == "personal-root"
    assert initial[ids[3]].drive_id == "shared"
    provider.records[ids[0]] = HttpError(Response({"status": "404"}), b"{}")
    provider.records[ids[1]] = {
        "id": ids[1],
        "name": "Trashed",
        "mimeType": MIME_DOC,
        "trashed": True,
    }
    updated = await service.get_search_metadata_many(ids)
    assert list(updated) == ids[2:]
    assert len(provider.requests) == 12
    assert provider.batches == 2


class _TrashProvider(_SingleProvider):
    def __init__(self, *, revoked: bool = False) -> None:
        super().__init__(
            HttpError(Response({"status": "404"}), b"{}")
            if revoked
            else {"id": "document", "name": "Document", "mimeType": MIME_DOC, "driveId": "shared"}
        )
        self.updates: list[dict[str, JsonValue]] = []

    def update(self, **params: JsonValue) -> _SingleRequest:
        self.updates.append(params)
        return _SingleRequest({"id": "document"})


async def test_trash_response_projection_keeps_fresh_source_and_one_mutation() -> None:
    provider = _TrashProvider()
    result = await _SingleFiles(_CountingDrive(), provider).delete(FileId("document"))
    assert result.source.name == "Document"
    assert len(provider.fields) == 1
    assert provider.updates == [
        {"fileId": "document", "body": {"trashed": True}, "fields": "id", "supportsAllDrives": True}
    ]


async def test_revoked_delete_never_reaches_projected_mutation() -> None:
    provider = _TrashProvider(revoked=True)
    with pytest.raises(DriveUpstreamError) as raised:
        await _SingleFiles(_CountingDrive(), provider).delete(FileId("document"))
    assert raised.value.http_status == 404
    assert provider.updates == []


class _PermissionDrive(GoogleDriveService):
    def __init__(self, client: _FakeClient) -> None:
        super().__init__(_user())
        self.client = client

    @override
    def _client(self) -> DriveResource:
        client: DriveResource = dependency(self.client)
        return client


async def test_permission_projection_keeps_two_fresh_metadata_reads_and_inheritance() -> None:
    metadata = {
        "id": "folder",
        "mimeType": MIME_FOLDER,
        "version": "17",
        "ownedByMe": True,
        "shared": False,
        "capabilities": {"canAddChildren": True},
        "owners": [{"emailAddress": "owner@example.test"}],
    }
    client = _FakeClient(
        files_pages=[metadata, metadata],
        permissions_pages=[
            {
                "permissions": [
                    {
                        "id": "owner",
                        "type": "user",
                        "role": "owner",
                        "permissionDetails": [
                            {"inherited": False, "permissionType": "file", "role": "owner"}
                        ],
                    }
                ]
            }
        ],
    )
    snapshot = await _PermissionDrive(client).read_file_permissions_bounded(
        FileId("folder"), max_pages=1, max_permissions=1
    )
    assert snapshot.file_id == "folder"
    assert snapshot.permissions[0].role == "owner"
    assert not snapshot.permissions[0].inherited
    assert snapshot.owner_emails == ("owner@example.test",)
    assert len(client.files_captured) == 2
    assert len(client.permissions_captured) == 1
    fields = client.permissions_captured[0]["fields"]
    assert "permissionDetails(inherited,permissionType,role)" in fields
    assert "inheritedFrom" not in fields
