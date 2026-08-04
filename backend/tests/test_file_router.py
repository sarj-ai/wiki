from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.drive.schemas import DriveNode, DriveSummary, DriveTree
from app.drive.service import DriveService
from app.files.router import read_file
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import DriveId, FileId
from app.sidebar.cache import CachedDriveTree, SidebarCacheService


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


class _Cache:
    def __init__(self) -> None:
        self.loaded: list[DriveId] = []

    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
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
async def test_read_file_rebuilds_invalidated_tree_for_complete_breadcrumb() -> None:
    cache = _Cache()

    detail = await read_file(
        FileId("doc-1"),
        cast("AsyncSession", None),
        cast("FilesService", _Files()),
        cast("DriveService", _Drive()),
        cast("SidebarCacheService", cache),
    )

    assert cache.loaded == [DriveId("drive-1")]
    assert [item.name for item in detail.breadcrumb] == [
        "My Drive",
        "Personal",
        "Identity & Immigration",
    ]
