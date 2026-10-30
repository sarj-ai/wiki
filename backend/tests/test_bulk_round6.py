from __future__ import annotations

import asyncio
import threading

from pydantic import JsonValue, ValidationError
import pytest

from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.files.errors import DriveUpstreamError
from app.identifiers import DriveId, FileId
from tests.bulk_round6_support import (
    BulkDrive,
    BulkFiles,
    BulkProvider,
    InventoryDrive,
    InventoryProvider,
    bulk_http_error,
)


def document_records(count: int) -> dict[FileId, JsonValue | BaseException]:
    return {
        FileId(f"file-{index}"): {
            "id": f"file-{index}",
            "name": f"Document {index}",
            "mimeType": MIME_DOC,
            "driveId": "shared",
            "parents": ["folder"],
        }
        for index in range(count)
    }


async def test_bulk_response_construction_keeps_final_scope_and_current_capabilities() -> None:
    records: dict[FileId, JsonValue | BaseException] = {
        FileId("shared"): {
            "id": "shared",
            "name": "Current 日本語",
            "mimeType": MIME_DOC,
            "driveId": "shared",
            "parents": ["folder"],
            "description": "Current description",
            "modifiedTime": "2026-10-11T12:00:00Z",
            "capabilities": {"canEdit": True, "canAddChildren": False},
        },
        FileId("personal"): {
            "id": "personal",
            "name": "Owned",
            "mimeType": MIME_FOLDER,
            "parents": ["personal-root"],
            "capabilities": {"canEdit": False, "canAddChildren": True},
        },
        FileId("foreign"): {
            "id": "foreign",
            "name": "Outside",
            "mimeType": MIME_DOC,
            "driveId": "outside",
        },
        FileId("wrong"): {"id": "different", "name": "Wrong", "mimeType": MIME_DOC},
        FileId("trashed"): {
            "id": "trashed",
            "name": "Gone",
            "mimeType": MIME_DOC,
            "trashed": True,
        },
        FileId("denied"): bulk_http_error(403),
        FileId("missing"): bulk_http_error(404),
    }
    provider = BulkProvider(records)
    drive = BulkDrive()
    files = BulkFiles(provider, drive)
    result = await files.get_accessible_many([*records, FileId("shared")])
    assert list(result) == ["shared", "personal"]
    assert result[FileId("shared")].model_dump() == {
        "id": "shared",
        "name": "Current 日本語",
        "mime_type": MIME_DOC,
        "parent_folder_id": "folder",
        "drive_id": "shared",
        "modified_time": "2026-10-11T12:00:00Z",
        "can_edit": True,
        "can_add_children": False,
        "description": "Current description",
    }
    assert result[FileId("personal")].drive_id == "personal-root"
    assert result[FileId("personal")].can_add_children
    assert drive.inventory_reads == 1
    assert len(provider.requests) == len(records)
    provider.records[FileId("shared")] = bulk_http_error(403)
    second = await files.get_accessible_many(list(records))
    assert list(second) == ["personal"]
    assert drive.inventory_reads == 2


async def test_out_of_scope_bulk_metadata_is_still_validated_before_filtering() -> None:
    provider = BulkProvider(
        {
            FileId("invalid"): {
                "id": "invalid",
                "name": {},
                "mimeType": MIME_DOC,
                "driveId": "outside",
            }
        }
    )
    drive = BulkDrive()
    with pytest.raises(ValidationError):
        await BulkFiles(provider, drive).get_accessible_many([FileId("invalid")])
    assert drive.inventory_reads == 0


async def test_large_bulk_read_retains_only_four_provider_worker_owners() -> None:
    provider = BulkProvider(document_records(5_001))
    provider.release = threading.Event()
    drive = BulkDrive()
    files = BulkFiles(provider, drive)
    load = asyncio.create_task(files.get_accessible_ids(list(provider.records)))
    try:
        assert await asyncio.to_thread(provider.started.wait, 2)
        assert len(files._metadata_reads) == 4
        assert provider.active == provider.peak_active == 4
    finally:
        provider.release.set()
    result = await asyncio.wait_for(load, timeout=3)
    assert result == set(provider.records)
    assert provider.batch_sizes.count(100) == 50
    assert provider.batch_sizes.count(1) == 1
    assert provider.peak_active == 4
    assert drive.inventory_reads == 1


async def test_cancelled_window_keeps_running_slots_and_stops_queued_batches() -> None:
    provider = BulkProvider(document_records(5_001))
    provider.release = threading.Event()
    drive = BulkDrive()
    files = BulkFiles(provider, drive)
    load = asyncio.create_task(files.get_accessible_many(list(provider.records)))
    try:
        assert await asyncio.to_thread(provider.started.wait, 2)
        workers = list(files._metadata_reads)
        load.cancel()
        with pytest.raises(asyncio.CancelledError):
            await load
        assert provider.active == 4
        assert files._metadata_slots.locked()
        assert drive.inventory_reads == 0
    finally:
        provider.release.set()
    await asyncio.wait_for(asyncio.gather(*workers), timeout=3)
    assert provider.batches == 4
    assert provider.active == 0
    assert drive.inventory_reads == 0


async def test_window_keeps_chunk_order_for_competing_provider_failures() -> None:
    provider = BulkProvider(document_records(401))
    provider.records[FileId("file-0")] = bulk_http_error(418)
    provider.records[FileId("file-100")] = bulk_http_error(401)
    drive = BulkDrive()
    with pytest.raises(DriveUpstreamError) as caught:
        await BulkFiles(provider, drive).get_accessible_many(list(provider.records))
    assert caught.value.http_status == 418
    assert caught.value.reason == "fixtureReason"
    assert drive.inventory_reads == 0


async def test_inventory_validation_keeps_order_and_complete_field_values() -> None:
    folders: list[dict[str, JsonValue]] = [
        {
            "id": f"folder-{index}",
            "name": f"Folder 日本語 {index}",
            "mimeType": MIME_FOLDER,
            "parents": ["shared"],
            "modifiedTime": "2026-10-11T12:00:00Z",
        }
        for index in range(129)
    ]
    files: list[dict[str, JsonValue]] = [
        {
            "id": f"document-{index}",
            "name": f"Current document {index}",
            "mimeType": MIME_DOC,
            "parents": ["folder-0"],
            "modifiedTime": "2026-10-11T12:00:00Z",
        }
        for index in range(129)
    ]
    provider = InventoryProvider(folders=folders, files=files)
    tree = await InventoryDrive(provider).walk_drive_folders(DriveId("shared"))
    assert tree.files_complete
    assert [node.id for node in tree.folders] == [row["id"] for row in folders]
    assert [node.id for node in tree.files] == [row["id"] for row in files]
    assert tree.folders[-1].name == "Folder 日本語 128"
    assert tree.folders[-1].parent_id is None
    assert tree.files[-1].parent_id == "folder-0"
    assert tree.files[-1].modified_time == "2026-10-11T12:00:00Z"
    assert len(provider.calls) == 2


@pytest.mark.parametrize("folders_invalid", [False, True])
async def test_inventory_validation_rejects_invalid_metadata_after_the_first_buffer(
    folders_invalid: bool,
) -> None:
    rows: list[dict[str, JsonValue]] = [
        {
            "id": f"node-{index}",
            "name": "Node",
            "mimeType": MIME_FOLDER if folders_invalid else MIME_DOC,
            "parents": ["shared"],
        }
        for index in range(65)
    ]
    rows[-1]["parents"] = [None]
    provider = InventoryProvider(
        folders=rows if folders_invalid else [], files=[] if folders_invalid else rows
    )
    with pytest.raises(ValidationError):
        await InventoryDrive(provider).walk_drive_folders(DriveId("shared"))
