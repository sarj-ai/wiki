from __future__ import annotations

from typing import override

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.drive.schemas import DriveNode, DriveSummary, DriveTree
from app.files.router import read_file
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId
from app.sidebar.cache import CachedDriveTree
from tests.support import dependency


class _Files:
    async def get(self, file_id: FileId) -> FileResponse:
        assert file_id == "doc-1"
        return FileResponse(
            id=file_id,
            name="Moved document",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("child-folder"),
            drive_id=DriveId("drive-1"),
            modified_time=None,
        )


class _Drive:
    async def list_drives(self) -> list[DriveSummary]:
        return [DriveSummary(id=DriveId("drive-1"), name="My Drive")]


class _RootFiles(_Files):
    def __init__(self, parent_id: FileId | None) -> None:
        self.parent_id = parent_id
        self.fetched: list[FileId] = []

    @override
    async def get(self, file_id: FileId) -> FileResponse:
        self.fetched.append(file_id)
        file = await super().get(file_id)
        return file.model_copy(update={"parent_folder_id": self.parent_id})


class _Cache:
    def __init__(self) -> None:
        self.loaded: list[DriveId] = []

    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        raise AssertionError

    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        self.loaded.append(drive_id)
        tree = DriveTree(
            drive_id=drive_id,
            folders=[
                DriveNode(
                    id=FileId("parent-folder"),
                    name="Personal",
                    mime_type="application/vnd.google-apps.folder",
                    parent_id=None,
                    modified_time=None,
                ),
                DriveNode(
                    id=FileId("child-folder"),
                    name="Identity & Immigration",
                    mime_type="application/vnd.google-apps.folder",
                    parent_id=FileId("parent-folder"),
                    modified_time=None,
                ),
            ],
            files=[],
            files_complete=True,
        )
        return CachedDriveTree(tree=tree, refreshed_at=None)


@pytest.mark.asyncio
async def test_read_file_only_peeks_cached_tree_for_complete_breadcrumb() -> None:
    cache = _Cache()

    detail = await read_file(
        FileId("doc-1"),
        dependency(None),
        dependency(_Files()),
        dependency(_Drive()),
        dependency(cache),
    )

    assert cache.loaded == [DriveId("drive-1")]
    assert [item.name for item in detail.breadcrumb] == [
        "My Drive",
        "Personal",
        "Identity & Immigration",
    ]


@pytest.mark.parametrize("parent_id", [None, FileId("drive-1")], ids=["missing", "root"])
async def test_read_file_root_breadcrumb_keeps_fresh_metadata_without_loading_tree(
    parent_id: FileId | None,
) -> None:
    cache = _Cache()
    files = _RootFiles(parent_id)
    detail = await read_file(
        FileId("doc-1"),
        dependency(None),
        dependency(files),
        dependency(_Drive()),
        dependency(cache),
    )

    assert files.fetched == [FileId("doc-1")]
    assert cache.loaded == []
    assert [(item.id, item.name) for item in detail.breadcrumb] == [("drive-1", "My Drive")]
