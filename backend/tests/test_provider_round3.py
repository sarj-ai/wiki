from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING, Self, override
import uuid

from googleapiclient.errors import HttpError
from pydantic import JsonValue
import pytest

from app.aliases.service import PostgresAliasService
from app.drive.schemas import MIME_DOC, MIME_FOLDER, DriveSummary
from app.drive.service import reuse_drive_client
from app.files.dependencies import require_accessible_doc
from app.files.errors import DriveUpstreamError, FileNotInScopeError
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId
from app.templates.service import DriveTemplateCatalogService
from tests.support import dependency
from tests.test_aliases import _Session
from tests.test_provider_hot_paths import (
    _AncestorProvider,
    _http_error,
    _MembershipDrive,
    _MetadataBatch,
    _MetadataFiles,
    _MetadataProvider,
    _ReadRequest,
    _user,
)
from tests.test_templates import _Cache, _node, _TemplateDrive


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource

    from app.drive.service import GoogleRefreshHandler


class _CountingDrive(_MembershipDrive):
    def __init__(self) -> None:
        super().__init__(_AncestorProvider())
        self.inventory_reads = 0

    @override
    async def list_drives(self) -> list[DriveSummary]:
        self.inventory_reads += 1
        return await super().list_drives()


@pytest.mark.parametrize("access_only", [False, True], ids=["metadata", "ids"])
async def test_all_rejected_metadata_does_not_resolve_empty_membership(access_only: bool) -> None:
    drive = _CountingDrive()
    provider = _MetadataProvider(
        {
            FileId("denied"): _http_error(403),
            FileId("missing"): _http_error(404),
            FileId("trashed"): {
                "id": "trashed",
                "name": "Gone",
                "mimeType": MIME_DOC,
                "trashed": True,
            },
            FileId("mismatched"): {"id": "other", "name": "Other", "mimeType": MIME_DOC},
        }
    )
    files = _MetadataFiles(drive, provider)
    if access_only:
        assert await files.get_accessible_ids(list(provider.records)) == set()
    else:
        assert await files.get_accessible_many(list(provider.records)) == {}
    assert provider.batches == 1
    assert len(provider.requests) == 4
    assert drive.inventory_reads == 0


@dataclass(frozen=True)
class _SingleRequest:
    response: JsonValue | HttpError

    def execute(self) -> JsonValue:
        if isinstance(self.response, HttpError):
            raise self.response
        return self.response


class _SingleProvider:
    def __init__(self, response: JsonValue | HttpError) -> None:
        self.response = response
        self.fields: list[str] = []

    def files(self) -> Self:
        return self

    def get(self, **params: JsonValue) -> _SingleRequest:
        assert params["fileId"] == "document"
        assert params["supportsAllDrives"] is True
        fields = params["fields"]
        assert isinstance(fields, str)
        self.fields.append(fields)
        return _SingleRequest(self.response)


class _SingleFiles(GoogleFilesService):
    def __init__(self, drive: _CountingDrive, provider: _SingleProvider) -> None:
        super().__init__(user=_user(), drive=drive)
        self.provider = provider

    @override
    def _client(self) -> DriveResource:
        client: DriveResource = dependency(self.provider)
        return client


async def test_access_only_route_and_alias_reads_project_fresh_scope() -> None:
    drive = _CountingDrive()
    provider = _SingleProvider({"id": "document", "parents": ["common-parent"]})
    files = _SingleFiles(drive, provider)
    aliases = PostgresAliasService(db=dependency(_Session()), files=files, user=_user())

    assert (await aliases.get_for_file(FileId("document"))).alias is None
    assert await require_accessible_doc(FileId("document"), files) == "document"
    assert provider.fields == ["id,parents,driveId,trashed"] * 2
    assert drive.provider.reads[FileId("common-parent")] == 2
    provider.response = _http_error(403)
    with pytest.raises(DriveUpstreamError) as rejected:
        await require_accessible_doc(FileId("document"), files)
    assert rejected.value.http_status == 403
    assert len(provider.fields) == 3


@pytest.mark.parametrize("trashed", [False, True], ids=["wrong-id", "trashed"])
async def test_access_only_get_rejects_wrong_identity_or_trash_before_membership(
    trashed: bool,
) -> None:
    drive = _CountingDrive()
    provider = _SingleProvider(
        {"id": "document" if trashed else "wrong", "driveId": "shared", "trashed": trashed}
    )
    with pytest.raises(FileNotInScopeError):
        await _SingleFiles(drive, provider).require_access(FileId("document"))
    assert drive.inventory_reads == 0


@pytest.mark.parametrize("edit_only", [False, True], ids=["get", "can-edit"])
async def test_full_get_rejects_wrong_identity_before_member_resolution(edit_only: bool) -> None:
    drive = _CountingDrive()
    provider = _SingleProvider(
        {"id": "wrong", "name": "Other file", "mimeType": MIME_DOC, "driveId": "shared"}
    )
    files = _SingleFiles(drive, provider)
    read = files.can_edit if edit_only else files.get
    with pytest.raises(FileNotInScopeError):
        await read(FileId("document"))
    assert len(provider.fields) == 1
    assert drive.inventory_reads == 0


class _CoordinatedBatchProvider(_MetadataProvider):
    def __init__(self, *, fail: bool = False, refresh: Callable[[], None] | None = None) -> None:
        super().__init__(
            {
                FileId(f"file-{index}"): {
                    "id": f"file-{index}",
                    "name": str(index),
                    "mimeType": MIME_DOC,
                    "driveId": "shared",
                }
                for index in range(701)
            }
        )
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.failed = asyncio.Event()
        self.release = threading.Event()
        self.release_failure = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.fail = fail
        self.batch_sizes: list[int] = []
        self.refresh = refresh

    @override
    def new_batch_http_request(
        self, *, callback: Callable[[str, JsonValue, BaseException | None], None]
    ) -> _CoordinatedBatch:
        return _CoordinatedBatch(self, callback)


class _CoordinatedBatch(_MetadataBatch):
    def __init__(
        self,
        provider: _CoordinatedBatchProvider,
        callback: Callable[[str, JsonValue, BaseException | None], None],
    ) -> None:
        super().__init__(provider, callback)
        self.coordinated_provider = provider

    @override
    def execute(self) -> None:
        provider = self.coordinated_provider
        with provider.lock:
            provider.active += 1
            provider.peak = max(provider.peak, provider.active)
            provider.batch_sizes.append(len(self.requests))
            if provider.active == 4:
                provider.loop.call_soon_threadsafe(provider.started.set)
        try:
            if provider.fail and self.requests[0][0] == "file-0":
                assert provider.release_failure.wait(timeout=3)
                self.callback("file-0", None, _http_error(418))
                provider.loop.call_soon_threadsafe(provider.failed.set)
            else:
                assert provider.release.wait(timeout=3)
                if provider.refresh is not None:
                    provider.refresh()
                for request_id, _request in self.requests:
                    response = provider.records[FileId(request_id)]
                    if isinstance(response, HttpError):
                        self.callback(request_id, None, response)
                    else:
                        self.callback(request_id, response, None)
        finally:
            with provider.lock:
                provider.active -= 1


async def test_large_metadata_batches_overlap_four_workers_and_keep_order() -> None:
    drive = _CountingDrive()
    provider = _CoordinatedBatchProvider()
    client_threads: list[int] = []

    class _ThreadClient:
        def __init__(self) -> None:
            self.owner = threading.get_ident()
            client_threads.append(self.owner)

        def files(self) -> _MetadataProvider:
            assert threading.get_ident() == self.owner
            return provider

        def new_batch_http_request(
            self, *, callback: Callable[[str, JsonValue, BaseException | None], None]
        ) -> _CoordinatedBatch:
            assert threading.get_ident() == self.owner
            return provider.new_batch_http_request(callback=callback)

    def build_client(_user: object, _refresh_handler: GoogleRefreshHandler | None) -> DriveResource:
        client: DriveResource = dependency(_ThreadClient())
        return client

    class _ThreadFiles(GoogleFilesService):
        @override
        def _client(self) -> DriveResource:
            return reuse_drive_client(self._user, self._clients, client_factory=build_client)

    files = _ThreadFiles(user=_user(), drive=drive)
    load = asyncio.create_task(files.get_accessible_many([*provider.records, FileId("file-0")]))
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        assert not load.done()
    finally:
        provider.release.set()
    result = await load
    assert list(result) == list(provider.records)
    assert provider.peak == 4
    assert sorted(provider.batch_sizes) == [1, *([100] * 7)]
    assert len(provider.requests) == 701
    assert drive.inventory_reads == 1
    assert len(set(client_threads)) >= 4
    assert len(files._clients) == len(set(client_threads))
    provider.records[FileId("file-0")] = _http_error(403)
    assert await files.get_accessible_ids([FileId("file-0")]) == set()
    assert len(provider.requests) == 702
    assert drive.inventory_reads == 1


async def test_failed_batch_drains_admitted_reads_and_stops_queued_work() -> None:
    drive = _CountingDrive()
    provider = _CoordinatedBatchProvider(fail=True)
    files = _MetadataFiles(drive, provider)
    load = asyncio.create_task(files.get_accessible_many(list(provider.records)))
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        workers = tuple(files._metadata_reads)
        assert len(workers) == 4
        provider.release_failure.set()
        await asyncio.wait_for(provider.failed.wait(), timeout=2)
        # The provider callback can run before its thread result reaches the
        # service. Hold successful reads until the failed worker has finished
        # so queued-work assertions exercise an observed service failure.
        finished, _pending = await asyncio.wait(
            workers, timeout=2, return_when=asyncio.FIRST_COMPLETED
        )
        assert len(finished) == 1
        assert isinstance(next(iter(finished)).exception(), DriveUpstreamError)
        assert not load.done()
    finally:
        provider.release_failure.set()
        provider.release.set()
    with pytest.raises(DriveUpstreamError):
        await load
    assert provider.active == 0
    assert provider.peak == 4
    assert len(provider.batch_sizes) == 4
    assert drive.inventory_reads == 0


@pytest.mark.parametrize("late_failure", [False, True], ids=["late-success", "late-failure"])
async def test_canceling_large_read_holds_thread_slots_and_never_starts_queued_batches(
    late_failure: bool,
) -> None:
    drive = _CountingDrive()
    provider = _CoordinatedBatchProvider(fail=late_failure)
    files = _MetadataFiles(drive, provider)
    load = asyncio.create_task(files.get_accessible_ids(list(provider.records)))
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=2)
        load.cancel()
        with pytest.raises(asyncio.CancelledError):
            await load
        assert provider.active == 4
        assert files._metadata_slots.locked()
        assert len(provider.batch_sizes) == 4
        assert drive.inventory_reads == 0
    finally:
        provider.release_failure.set()
        provider.release.set()
    workers = list(files._metadata_reads)
    if workers:
        finished = await asyncio.wait_for(
            asyncio.gather(*workers, return_exceptions=True), timeout=2
        )
        assert sum(isinstance(result, DriveUpstreamError) for result in finished) == int(
            late_failure
        )
    assert provider.active == 0
    assert len(provider.batch_sizes) == 4
    assert not files._metadata_reads


async def test_targeted_template_validation_skips_unrelated_workspaces_and_stays_fresh() -> None:
    drive = _TemplateDrive()
    drive._drives.append(DriveSummary(id=DriveId("other-drive"), name="Other"))
    drive._children[FileId("other-drive")] = [
        _node("other-root", "Templates", MIME_FOLDER, "other-drive")
    ]
    drive._children[FileId("other-root")] = [
        _node("other-template", "Other", MIME_DOC, "other-root")
    ]
    service = DriveTemplateCatalogService(drive=drive, user_id=uuid.uuid7(), cache=_Cache())
    assert (await service.list_templates()).templates
    drive.calls.clear()

    selected = await service.resolve_template(FileId("prd"), DriveId("drive-1"))
    assert selected is not None
    assert selected.category_path == ["Product"]
    assert dict(drive.calls) == {
        FileId("drive-1"): 1,
        FileId("templates"): 1,
        FileId("prd-folder"): 1,
    }
    drive._children[FileId("prd-folder")] = []
    assert await service.resolve_template(FileId("prd"), DriveId("drive-1")) is None
    assert drive.calls[FileId("prd-folder")] == 2
    before = dict(drive.calls)
    assert await service.resolve_template(FileId("prd"), DriveId("foreign")) is None
    assert dict(drive.calls) == before


async def test_targeted_template_discovery_shares_only_current_same_user_workspace_flight() -> None:
    drive = _TemplateDrive()
    user_id = uuid.uuid7()
    services = [DriveTemplateCatalogService(drive=drive, user_id=user_id) for _ in range(5)]
    results = await asyncio.gather(
        *(service.resolve_template(FileId("prd"), DriveId("drive-1")) for service in services)
    )
    assert all(result is not None for result in results)
    assert sum(drive.calls.values()) == 3
    drive._children[FileId("prd-folder")] = []
    assert await services[0].resolve_template(FileId("prd"), DriveId("drive-1")) is None
    assert sum(drive.calls.values()) == 6


async def test_targeted_template_discovery_does_not_share_other_user_visibility() -> None:
    allowed = _TemplateDrive()
    restricted = _TemplateDrive()
    restricted._children[FileId("prd-folder")] = []
    allowed_service = DriveTemplateCatalogService(drive=allowed, user_id=uuid.uuid7())
    restricted_service = DriveTemplateCatalogService(drive=restricted, user_id=uuid.uuid7())
    results = await asyncio.gather(
        allowed_service.resolve_template(FileId("prd"), DriveId("drive-1")),
        restricted_service.resolve_template(FileId("prd"), DriveId("drive-1")),
    )
    assert results[0] is not None
    assert results[1] is None
    assert sum(allowed.calls.values()) == 3
    assert sum(restricted.calls.values()) == 3


async def test_targeted_template_discovery_isolates_workspaces_for_same_user() -> None:
    drive = _TemplateDrive()
    drive._drives.append(DriveSummary(id=DriveId("other-drive"), name="Other"))
    drive._children[FileId("other-drive")] = [
        _node("other-root", "Templates", MIME_FOLDER, "other-drive")
    ]
    drive._children[FileId("other-root")] = [
        _node("other-template", "Other", MIME_DOC, "other-root")
    ]
    service = DriveTemplateCatalogService(drive=drive, user_id=uuid.uuid7())
    selected, other = await asyncio.gather(
        service.resolve_template(FileId("prd"), DriveId("drive-1")),
        service.resolve_template(FileId("other-template"), DriveId("other-drive")),
    )
    assert selected is not None
    assert selected.drive_id == "drive-1"
    assert other is not None
    assert other.drive_id == "other-drive"
    assert sum(drive.calls.values()) == 5


async def test_targeted_template_discovery_rejects_fresh_provider_revocation() -> None:
    class _RevokedProvider(_AncestorProvider):
        def __init__(self) -> None:
            super().__init__()
            self.revoked = False
            self.pages = [
                {"files": [{"id": "templates", "name": "Templates", "mimeType": MIME_FOLDER}]},
                {
                    "files": [
                        {
                            "id": "document",
                            "name": "Template",
                            "mimeType": MIME_DOC,
                            "modifiedTime": "2026-10-11T00:00:00Z",
                        }
                    ]
                },
            ]

        @override
        def list(self, **params: JsonValue) -> _ReadRequest:
            if self.revoked:
                self.list_requests.append(params)
                return _ReadRequest(_http_error(403))
            return super().list(**params)

    provider = _RevokedProvider()
    service = DriveTemplateCatalogService(drive=_MembershipDrive(provider), user_id=uuid.uuid7())
    assert await service.resolve_template(FileId("document"), DriveId("shared")) is not None
    assert len(provider.list_requests) == 2
    provider.revoked = True
    assert await service.resolve_template(FileId("document"), DriveId("shared")) is None
    assert len(provider.list_requests) == 3
