from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, NamedTuple

from googleapiclient.errors import HttpError
import httplib2
import pytest
from tenacity import wait_none

from app.drive.service import GoogleDriveService
from app.identifiers import DriveId, FileId


class Request:
    def __init__(self, value: object) -> None:
        self.value = value

    def execute(self) -> object:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Resource:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []
        self.parent_response: object = {"id": "parent", "parents": ["personal-root"]}

    def get(self, **kwargs: object) -> Request:
        return Request(
            {"id": "personal-root"} if kwargs["fileId"] == "root" else self.parent_response
        )

    def list(self, **kwargs: Any) -> Request:
        self.calls.append(kwargs)
        return Request(self.response)


class Client:
    def __init__(self) -> None:
        self.inventory = Resource(
            {
                "drives": [{"id": f"drive-{i}", "name": f"Drive {i}"} for i in range(7)],
                "nextPageToken": "more",
            }
        )
        self.documents = Resource({"files": []})

    def files(self) -> Resource:
        return self.documents

    def drives(self) -> Resource:
        return self.inventory


class Services(NamedTuple):
    drive: GoogleDriveService
    client: Client


def service(monkeypatch: pytest.MonkeyPatch) -> Services:
    client = Client()
    user: Any = SimpleNamespace(id="test-user")
    drive = GoogleDriveService(user)
    monkeypatch.setattr(drive, "_client", lambda: client)
    return Services(drive, client)


async def test_bounded_discovery_search_and_acl_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    drive, client = service(monkeypatch)
    async with drive.search_bounded("policy", limit=50) as result:
        assert result.files == []
        assert result.truncated is True
        assert len(await drive.list_drives()) == 8
        assert await drive.resolve_member_drives({FileId("file"): DriveId("drive-1")}) == {
            FileId("file"): DriveId("drive-1")
        }
    assert len(client.inventory.calls) == 1
    assert len(client.documents.calls) == 8
    assert all(call["corpora"] != "allDrives" for call in client.documents.calls)


async def test_candidate_cap_stops_further_drives(monkeypatch: pytest.MonkeyPatch) -> None:
    drive, client = service(monkeypatch)
    client.documents.response = {
        "files": [
            {"id": f"file-{i}", "name": "Policy", "mimeType": "text/plain"} for i in range(50)
        ]
    }
    async with drive.search_bounded("policy") as result:
        assert len(result.files) == 50
    assert len(client.documents.calls) == 1


@pytest.mark.parametrize("operation", ["discovery", "search"])
async def test_provider_failure_is_not_empty_results(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    drive, client = service(monkeypatch)
    resource = client.inventory if operation == "discovery" else client.documents
    resource.response = HttpError(httplib2.Response({"status": "403"}), b"denied")
    with pytest.raises(HttpError):
        async with drive.search_bounded("policy"):
            pytest.fail("Provider failure became successful search")


@pytest.mark.parametrize("operation", ["discovery", "search"])
async def test_oversize_provider_page_rejected(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    drive, client = service(monkeypatch)
    if operation == "discovery":
        client.inventory.response = {"drives": [{}] * 8}
    else:
        client.documents.response = {"files": [{}] * 51}
    with pytest.raises(ValueError, match="Invalid bounded"):
        async with drive.search_bounded("policy"):
            pytest.fail("Oversize response accepted")


async def test_scope_restored_after_reader_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    drive, client = service(monkeypatch)
    message = "reader failed"
    with pytest.raises(RuntimeError, match="reader failed"):
        async with drive.search_bounded("policy"):
            raise RuntimeError(message)
    client.inventory.response = {"drives": []}
    assert len(await drive.list_drives()) == 1
    assert len(client.inventory.calls) == 2


async def test_cancelled_reader_restores_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    drive, client = service(monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        async with drive.search_bounded("policy"):
            raise asyncio.CancelledError
    client.inventory.response = {}
    assert len(await drive.list_drives()) == 1
    assert len(client.inventory.calls) == 2


async def test_empty_provider_collection_can_be_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    drive, client = service(monkeypatch)
    client.inventory.response = {}
    client.documents.response = {}
    async with drive.search_bounded("policy") as result:
        assert result.files == []
        assert result.truncated is True
    assert len(client.documents.calls) == 1


@pytest.mark.parametrize("status", [429, 503])
async def test_bounded_ancestry_provider_failure_propagates(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    drive, client = service(monkeypatch)
    client.documents.parent_response = HttpError(
        httplib2.Response({"status": str(status)}), b"upstream"
    )
    monkeypatch.setattr("app.drive.retry.execute_with_retry.retry.wait", wait_none())
    async with drive.search_bounded("policy"):
        with pytest.raises(HttpError):
            await drive.resolve_member_drives(
                {FileId("file"): None}, parent_ids={FileId("file"): FileId("parent")}
            )


@pytest.mark.parametrize("status", [403, 404])
async def test_bounded_ancestry_denial_remains_inaccessible(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    drive, client = service(monkeypatch)
    client.documents.parent_response = HttpError(
        httplib2.Response({"status": str(status)}), b"denied"
    )
    async with drive.search_bounded("policy"):
        assert (
            await drive.resolve_member_drives(
                {FileId("file"): None}, parent_ids={FileId("file"): FileId("parent")}
            )
            == {}
        )


@pytest.mark.parametrize(
    "response",
    [{"parents": []}, {"id": "wrong-id"}, {"id": "parent", "parents": 42}],
    ids=["missing-id", "wrong-id", "malformed-parents"],
)
async def test_bounded_ancestry_malformed_response_propagates(
    monkeypatch: pytest.MonkeyPatch, response: object
) -> None:
    drive, client = service(monkeypatch)
    client.documents.parent_response = response
    async with drive.search_bounded("policy"):
        with pytest.raises(ValueError, match=r"GoogleFileLocation|Invalid personal ancestry"):
            await drive.resolve_member_drives(
                {FileId("file"): None}, parent_ids={FileId("file"): FileId("parent")}
            )


@pytest.mark.parametrize(
    "response",
    [HttpError(httplib2.Response({"status": "503"}), b"upstream"), {"parents": []}],
    ids=["unavailable-provider", "malformed-parent"],
)
async def test_legacy_ancestry_failure_remains_inaccessible(
    monkeypatch: pytest.MonkeyPatch, response: object
) -> None:
    drive, client = service(monkeypatch)
    client.inventory.response = {"drives": []}
    client.documents.parent_response = response
    monkeypatch.setattr("app.drive.retry.execute_with_retry.retry.wait", wait_none())
    assert (
        await drive.resolve_member_drives(
            {FileId("file"): None}, parent_ids={FileId("file"): FileId("parent")}
        )
        == {}
    )
