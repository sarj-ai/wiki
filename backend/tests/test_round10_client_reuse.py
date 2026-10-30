from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import threading
from typing import TYPE_CHECKING, Literal, override

import pytest

from app.drive.schemas import MIME_DOC
from app.drive.service import GoogleDriveService
from app.files.service import GoogleFilesService
from app.identifiers import FileId, GoogleUserId
from tests.round7_provider_support import SHARED, DriveTransport, fixture_user


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource

    from app.models.user import User


class _CountingTransport(DriveTransport):
    def __init__(self) -> None:
        super().__init__(
            targets={
                "document": {
                    "id": "document",
                    "name": "Fixture document",
                    "mimeType": MIME_DOC,
                    "driveId": SHARED.id,
                }
            }
        )
        self.build_threads: list[int] = []

    @override
    def factory(self, _user: User, _refresh_handler: object | None) -> DriveResource:
        self.build_threads.append(threading.get_ident())
        return super().factory(_user, _refresh_handler)


async def test_native_serial_metadata_and_inventory_share_only_one_sdk_root() -> None:
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
    transport = _CountingTransport()
    user = fixture_user()
    factory = transport.factory
    drive = GoogleDriveService(user, client_factory=factory)
    files = GoogleFilesService(user=user, drive=drive, client_factory=factory)

    first = await files.get(FileId("document"))
    transport.targets = {
        "document": {
            "id": "document",
            "name": "Fresh rename",
            "mimeType": MIME_DOC,
            "driveId": SHARED.id,
        }
    }
    renamed = await files.get(FileId("document"))

    assert first.name == "Fixture document"
    assert renamed.name == "Fresh rename"
    assert len(transport.build_threads) == 1
    assert len([path for path, _ in transport.calls if path.endswith("/files/document")]) == 2


@pytest.mark.parametrize("changed", ["subject", "revision", "generation", "ciphertext", "expiry"])
async def test_native_metadata_client_rejects_changed_credential_binding(
    changed: Literal["subject", "revision", "generation", "ciphertext", "expiry"],
) -> None:
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
    transport = _CountingTransport()
    user = fixture_user()
    user.google_token_generation = 0
    factory = transport.factory
    drive = GoogleDriveService(user, client_factory=factory)
    await drive.list_drives()
    match changed:
        case "subject":
            user.google_id = GoogleUserId("different-subject")
        case "revision":
            user.token_version += 1
        case "generation":
            user.google_token_generation += 1
        case "ciphertext":
            user.encrypted_access_token = "different-synthetic-ciphertext"
        case "expiry":
            user.google_access_token_expires_at = datetime.now(UTC)
    files = GoogleFilesService(user=user, drive=drive, client_factory=factory)

    assert (await files.get(FileId("document"))).id == "document"
    assert len(transport.build_threads) == 2


async def test_native_metadata_client_rejects_another_user_object_and_factory() -> None:
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
    transport = _CountingTransport()
    user = fixture_user()
    factory = transport.factory
    drive = GoogleDriveService(user, client_factory=factory)
    await drive.list_drives()
    foreign = GoogleFilesService(user=fixture_user(), drive=drive, client_factory=factory)
    assert (await foreign.get(FileId("document"))).id == "document"
    assert len(transport.build_threads) == 2
    changed_factory = GoogleFilesService(user=user, drive=drive, client_factory=transport.factory)
    assert (await changed_factory.get(FileId("document"))).id == "document"
    assert len(transport.build_threads) == 3


async def test_native_metadata_client_preserves_subclass_client_dispatch() -> None:
    class CustomDrive(GoogleDriveService):
        pass

    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
    transport = _CountingTransport()
    user = fixture_user()
    factory = transport.factory
    drive = CustomDrive(user, client_factory=factory)
    await drive.list_drives()
    files = GoogleFilesService(user=user, drive=drive, client_factory=factory)

    assert (await files.get(FileId("document"))).id == "document"
    assert len(transport.build_threads) == 2
