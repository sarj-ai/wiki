from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING, Self, override
import uuid

from googleapiclient.errors import HttpError
from httplib2 import Response
from pydantic import JsonValue

from app.drive.schemas import DriveSummary
from app.drive.service import GoogleDriveService
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from tests.support import dependency


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource


def bulk_user() -> User:
    return User(
        id=UserId(uuid.uuid7()),
        google_id=GoogleUserId("round6-bulk-fixture"),
        email="round6-bulk@example.com",
        token_version=0,
    )


def bulk_http_error(status: int) -> HttpError:
    return HttpError(
        Response({"status": str(status)}),
        b'{"error":{"errors":[{"reason":"fixtureReason"}]}}',
    )


@dataclass(frozen=True)
class BulkRequest:
    provider: BulkProvider
    file_id: FileId
    fields: str

    def execute(self) -> JsonValue:
        with self.provider.lock:
            self.provider.direct_reads += 1
        response = self.provider.response(self)
        if isinstance(response, BaseException):
            raise response
        return response


class BulkBatch:
    def __init__(
        self,
        provider: BulkProvider,
        callback: Callable[[str, JsonValue, BaseException | None], None],
    ) -> None:
        self.provider = provider
        self.callback = callback
        self.requests: list[tuple[str, BulkRequest]] = []

    def add(self, request: BulkRequest, *, request_id: str) -> None:
        self.requests.append((request_id, request))

    def execute(self) -> None:
        provider = self.provider
        with provider.lock:
            provider.batches += 1
            provider.active += 1
            provider.peak_active = max(provider.active, provider.peak_active)
            provider.batch_sizes.append(len(self.requests))
            if provider.active == provider.expected_active:
                provider.started.set()
        try:
            if provider.release is not None and not provider.release.wait(timeout=4):
                raise TimeoutError
            for request_id, request in self.requests:
                response = provider.response(request)
                if isinstance(response, BaseException):
                    self.callback(request_id, None, response)
                else:
                    self.callback(request_id, response, None)
        finally:
            with provider.lock:
                provider.active -= 1


class BulkProvider:
    def __init__(self, records: dict[FileId, JsonValue | BaseException]) -> None:
        self.records = records
        self.requests: list[BulkRequest] = []
        self.batches = 0
        self.direct_reads = 0
        self.batch_sizes: list[int] = []
        self.active = 0
        self.peak_active = 0
        self.expected_active = 4
        self.lock = threading.Lock()
        self.started = threading.Event()
        self.release: threading.Event | None = None

    def files(self) -> Self:
        return self

    def get(self, **params: JsonValue) -> BulkRequest:
        file_id, fields = params["fileId"], params["fields"]
        assert isinstance(file_id, str)
        assert isinstance(fields, str)
        assert params["supportsAllDrives"] is True
        request = BulkRequest(self, FileId(file_id), fields)
        with self.lock:
            self.requests.append(request)
        return request

    def response(self, request: BulkRequest) -> JsonValue | BaseException:
        raw = self.records[request.file_id]
        if not isinstance(raw, dict):
            return raw
        return {
            key: value
            for key, value in raw.items()
            if key in request.fields.split(",") or f"{key}(" in request.fields
        }

    def new_batch_http_request(
        self,
        *,
        callback: Callable[[str, JsonValue, BaseException | None], None],
    ) -> BulkBatch:
        return BulkBatch(self, callback)


class BulkDrive(GoogleDriveService):
    def __init__(self, provider: BulkProvider | None = None) -> None:
        super().__init__(bulk_user())
        self.provider = provider
        self.inventory_reads = 0
        self.drives = [
            DriveSummary(id=DriveId("personal-root"), name="My Drive", is_personal=True),
            DriveSummary(id=DriveId("shared"), name="Shared"),
        ]

    @override
    async def list_drives(self) -> list[DriveSummary]:
        self.inventory_reads += 1
        return self.drives

    @override
    def _client(self) -> DriveResource:
        if self.provider is None:
            raise AssertionError
        client: DriveResource = dependency(self.provider)
        return client


class BulkFiles(GoogleFilesService):
    def __init__(self, provider: BulkProvider, drive: GoogleDriveService | None = None) -> None:
        super().__init__(user=bulk_user(), drive=drive if drive is not None else BulkDrive())
        self.provider = provider

    @override
    def _client(self) -> DriveResource:
        client: DriveResource = dependency(self.provider)
        return client


@dataclass(frozen=True)
class InventoryRequest:
    response: JsonValue

    def execute(self) -> JsonValue:
        return self.response


class InventoryProvider:
    def __init__(
        self,
        *,
        folders: builtins.list[dict[str, JsonValue]],
        files: builtins.list[dict[str, JsonValue]],
    ) -> None:
        self.folder_rows = folders
        self.file_rows = files
        self.calls: list[dict[str, JsonValue]] = []

    def files(self) -> Self:
        return self

    def list(self, **params: JsonValue) -> InventoryRequest:
        self.calls.append(params)
        query = params["q"]
        assert isinstance(query, str)
        assert params["corpora"] == "drive"
        assert params["driveId"] == "shared"
        assert params["supportsAllDrives"] is True
        assert params["includeItemsFromAllDrives"] is True
        rows = self.file_rows if "mimeType!=" in query else self.folder_rows
        return InventoryRequest({"files": list[JsonValue](rows)})


class InventoryDrive(GoogleDriveService):
    def __init__(self, provider: InventoryProvider) -> None:
        super().__init__(bulk_user())
        self.provider = provider

    @override
    async def list_drives(self) -> list[DriveSummary]:
        return [DriveSummary(id=DriveId("shared"), name="Shared")]

    @override
    def _client(self) -> DriveResource:
        client: DriveResource = dependency(self.provider)
        return client
