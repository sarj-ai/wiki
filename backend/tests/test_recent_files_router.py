from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated
import uuid

from fastapi import Depends, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import get_current_user
from app.drive.dependencies import drive_service_dep
from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SHORTCUT, DriveSummary
from app.drive.service import DriveService
from app.files.dependencies import files_service_dep
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.main import app
from app.models.user import User
from tests.support import dependency


class _RecentFiles:
    def __init__(self) -> None:
        self.reads: list[list[FileId]] = []
        self.accessible = {
            FileId(f"file-{index}"): _file(FileId(f"file-{index}")) for index in range(6)
        }

    async def get_search_metadata_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        self.reads.append(file_ids)
        return {
            file_id: self.accessible[file_id] for file_id in file_ids if file_id in self.accessible
        }


class _RecentDrive:
    async def list_drives(self) -> list[DriveSummary]:
        return [DriveSummary(id=DriveId("drive-1"), name="Engineering")]


def _file(file_id: FileId, mime_type: str = MIME_DOC) -> FileResponse:
    return FileResponse(
        id=FileId(file_id),
        name=f"Current {file_id}",
        mime_type=mime_type,
        drive_id=DriveId("drive-1"),
        parent_folder_id=None,
        modified_time=None,
    )


def _authenticated() -> User:
    return User(
        id=UserId(uuid.uuid7()),
        email="recent@sarj.ai",
        google_id=GoogleUserId("recent"),
        token_version=0,
    )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def recent_files() -> _RecentFiles:
    files = _RecentFiles()

    async def provide_files(user: Annotated[User, Depends(get_current_user)]) -> FilesService:
        assert user.google_id == GoogleUserId("recent")
        return dependency(files)

    async def provide_drive(user: Annotated[User, Depends(get_current_user)]) -> DriveService:
        assert user.google_id == GoogleUserId("recent")
        return dependency(_RecentDrive())

    app.dependency_overrides[get_current_user] = _authenticated
    app.dependency_overrides[files_service_dep] = provide_files
    app.dependency_overrides[drive_service_dep] = provide_drive
    return files


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_recent_files_batch_preserves_order_deduplicates_and_returns_five(
    recent_files: _RecentFiles,
) -> None:
    ids = ["file-5", "file-4", "file-3", "file-2", "file-1", "file-0"]
    async with _client() as client:
        response = await client.get(
            "/files/recent", params=[("file_ids", file_id) for file_id in ids]
        )
        duplicate = await client.get(
            "/files/recent", params=[("file_ids", "file-1"), ("file_ids", "file-1")]
        )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert [file["id"] for file in response.json()["pages"]] == ids[:5]
    assert all(file["drive_name"] == "Engineering" for file in response.json()["pages"])
    assert set(response.json()["pages"][0]) == {"id", "name", "mime_type", "drive_name"}
    assert [file["id"] for file in duplicate.json()["pages"]] == ["file-1"]
    assert recent_files.reads == [ids, ["file-1"]]


async def test_recent_files_fills_from_sixth_when_most_recent_access_is_revoked(
    recent_files: _RecentFiles,
) -> None:
    recent_files.accessible = {
        file_id: file
        for file_id, file in recent_files.accessible.items()
        if file_id != FileId("file-5")
    }
    ids = [f"file-{index}" for index in reversed(range(6))]
    async with _client() as client:
        response = await client.get(
            "/files/recent", params=[("file_ids", file_id) for file_id in ids]
        )
    assert response.status_code == 200
    assert [file["id"] for file in response.json()["pages"]] == ids[1:]
    assert "file-5" not in response.text


async def test_recent_files_rechecks_access_and_names_each_landing(
    recent_files: _RecentFiles,
) -> None:
    async with _client() as client:
        first = await client.get("/files/recent", params={"file_ids": "file-1"})
        recent_files.accessible[FileId("file-1")].name = "Renamed now"
        renamed = await client.get("/files/recent", params={"file_ids": "file-1"})
        recent_files.accessible.clear()
        revoked = await client.get("/files/recent", params={"file_ids": "file-1"})
    assert first.json()["pages"][0]["name"] == "Current file-1"
    assert renamed.json()["pages"][0]["name"] == "Renamed now"
    assert revoked.json() == {"pages": []}
    assert len(recent_files.reads) == 3


async def test_recent_files_never_includes_folders_or_shortcuts(recent_files: _RecentFiles) -> None:
    recent_files.accessible[FileId("file-0")] = _file(FileId("file-0"), MIME_FOLDER)
    recent_files.accessible[FileId("file-1")] = _file(FileId("file-1"), MIME_SHORTCUT)
    async with _client() as client:
        response = await client.get(
            "/files/recent", params=[("file_ids", "file-0"), ("file_ids", "file-1")]
        )
    assert response.status_code == 200
    assert response.json() == {"pages": []}


@pytest.mark.parametrize("ids", [[], ["file-1"] * 7, [""], ["private/path"], ["x" * 129]])
async def test_recent_files_bounds_and_validates_ids_before_provider_reads(
    recent_files: _RecentFiles, ids: list[str]
) -> None:
    async with _client() as client:
        response = await client.get(
            "/files/recent", params=[("file_ids", file_id) for file_id in ids]
        )
    assert response.status_code == 422
    assert recent_files.reads == []


async def test_recent_files_requires_authentication_before_provider_reads(
    recent_files: _RecentFiles,
) -> None:
    async def unauthorized() -> User:
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_current_user] = unauthorized
    async with _client() as client:
        response = await client.get("/files/recent", params={"file_ids": "file-1"})
    assert response.status_code == 401
    assert recent_files.reads == []
