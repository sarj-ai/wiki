from __future__ import annotations

from collections import defaultdict
from typing import override
from uuid import uuid4

import pytest

from app.drive.schemas import (
    MIME_DOC,
    MIME_FOLDER,
    MIME_SLIDE,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
    SnippetResult,
)
from app.drive.service import DriveService
from app.identifiers import DriveId, FileId
from app.templates.service import DriveTemplateCatalogService, clear_template_catalog_cache


class _TemplateDrive(DriveService):
    def __init__(self) -> None:
        self.calls: defaultdict[FileId, int] = defaultdict(int)
        self._drives = [DriveSummary(id=DriveId("drive-1"), name="Product")]
        self._children = {
            FileId("drive-1"): [
                _node("templates", "Templates", MIME_FOLDER, "drive-1"),
                _node("other", "templates", MIME_FOLDER, "drive-1"),
            ],
            FileId("templates"): [
                _node("prd-folder", "Product", MIME_FOLDER, "templates"),
                _node("slides", "Company Update", MIME_SLIDE, "templates"),
                _node("pdf", "Ignored", "application/pdf", "templates"),
            ],
            FileId("prd-folder"): [
                _node("prd", "PRD", MIME_DOC, "prd-folder", description="Product brief"),
            ],
        }

    @override
    async def list_drives(self) -> list[DriveSummary]:
        return self._drives

    @override
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
        raise NotImplementedError

    @override
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        self.calls[folder_id] += 1
        return self._children.get(folder_id, [])

    @override
    async def search_drive(
        self, drive_id: DriveId, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        raise NotImplementedError

    @override
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        raise NotImplementedError

    @override
    async def fetch_snippet(
        self, file_id: FileId, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_template_catalog_cache()


async def test_discovers_only_native_templates_under_exact_root_folder() -> None:
    drive = _TemplateDrive()
    service = DriveTemplateCatalogService(drive=drive, user_id=uuid4())

    catalog = await service.list_templates()

    assert [template.file_id for template in catalog.templates] == ["slides", "prd"]
    prd = catalog.templates[1]
    assert prd.category_path == ["Product"]
    assert prd.description == "Product brief"
    assert prd.mime_type == MIME_DOC
    assert drive.calls[FileId("other")] == 0


async def test_caches_catalog_until_explicit_refresh() -> None:
    drive = _TemplateDrive()
    service = DriveTemplateCatalogService(drive=drive, user_id=uuid4())

    first = await service.list_templates()
    drive._children[FileId("templates")].append(_node("memo", "Memo", MIME_DOC, "templates"))
    second = await service.list_templates()
    refreshed = await service.list_templates(refresh=True)

    assert first == second
    assert [template.file_id for template in refreshed.templates] == ["slides", "memo", "prd"]
    assert drive.calls[FileId("drive-1")] == 2


def _node(
    file_id: str,
    name: str,
    mime_type: str,
    parent_id: str,
    *,
    description: str | None = None,
) -> DriveNode:
    return DriveNode(
        id=FileId(file_id),
        name=name,
        mime_type=mime_type,
        parent_id=FileId(parent_id),
        modified_time="2026-08-02T00:00:00Z",
        description=description,
    )
