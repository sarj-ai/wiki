from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import timedelta
from typing import override
from uuid import uuid7

from pydantic import JsonValue

from app.cache.service import CacheNamespace, CacheService
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
from app.templates.service import DriveTemplateCatalogService


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


class _Cache(CacheService):
    def __init__(self) -> None:
        self.values: dict[tuple[CacheNamespace, str], JsonValue] = {}

    @override
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None:
        return self.values.get((namespace, key))

    @override
    async def get_many(self, namespace: CacheNamespace, keys: list[str]) -> dict[str, JsonValue]:
        return {
            key: value for key in keys if (value := self.values.get((namespace, key))) is not None
        }

    @override
    async def put(
        self,
        namespace: CacheNamespace,
        key: str,
        value: JsonValue,
        *,
        ttl: timedelta,
    ) -> None:
        self.values[namespace, key] = value

    @override
    async def put_many(
        self,
        namespace: CacheNamespace,
        values: Mapping[str, JsonValue],
        *,
        ttl: timedelta,
    ) -> None:
        for key, value in values.items():
            self.values[namespace, key] = value

    @override
    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        self.values.pop((namespace, key), None)


async def test_discovers_only_native_templates_under_exact_root_folder() -> None:
    drive = _TemplateDrive()
    drive._children[FileId("drive-1")].append(_node("document", "Templates", MIME_DOC, "drive-1"))
    service = DriveTemplateCatalogService(drive=drive, user_id=uuid7())

    catalog = await service.list_templates()

    assert [template.file_id for template in catalog.templates] == ["slides", "prd"]
    prd = catalog.templates[1]
    assert prd.category_path == ["Product"]
    assert prd.description == "Product brief"
    assert prd.mime_type == MIME_DOC
    assert drive.calls[FileId("other")] == 0
    assert drive.calls[FileId("document")] == 0


async def test_discovers_each_duplicate_named_template_root() -> None:
    drive = _TemplateDrive()
    drive._children[FileId("drive-1")].append(
        _node("templates-2", "Templates", MIME_FOLDER, "drive-1")
    )
    drive._children[FileId("templates-2")] = [
        _node("duplicate-root-doc", "Additional template", MIME_DOC, "templates-2")
    ]

    catalog = await DriveTemplateCatalogService(drive=drive, user_id=uuid7()).list_templates()

    assert {template.file_id for template in catalog.templates} == {
        "slides",
        "prd",
        "duplicate-root-doc",
    }
    assert drive.calls[FileId("templates")] == 1
    assert drive.calls[FileId("templates-2")] == 1


async def test_caches_catalog_until_explicit_refresh() -> None:
    drive = _TemplateDrive()
    service = DriveTemplateCatalogService(drive=drive, user_id=uuid7(), cache=_Cache())

    first = await service.list_templates()
    drive._children[FileId("templates")].append(_node("memo", "Memo", MIME_DOC, "templates"))
    second = await service.list_templates()
    refreshed = await service.list_templates(refresh=True)

    assert first == second
    assert [template.file_id for template in refreshed.templates] == ["slides", "memo", "prd"]
    assert drive.calls[FileId("drive-1")] == 2


async def test_template_categories_load_concurrently_with_bounded_provider_work() -> None:
    class DelayedDrive(_TemplateDrive):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak_active = 0
            self._children[FileId("templates")] = [
                _node(f"category-{index}", f"Category {index}", MIME_FOLDER, "templates")
                for index in range(20)
            ]
            for index in range(20):
                self._children[FileId(f"category-{index}")] = [
                    _node(f"template-{index}", f"Template {index}", MIME_DOC, f"category-{index}"),
                    _node("templates", "Cycle", MIME_FOLDER, f"category-{index}"),
                ]

        @override
        async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            try:
                await asyncio.sleep(0.002)
                return await super().list_folder_children(folder_id)
            finally:
                self.active -= 1

    drive = DelayedDrive()
    catalog = await DriveTemplateCatalogService(drive=drive, user_id=uuid7()).list_templates()

    assert len(catalog.templates) == 20
    assert drive.peak_active == 8
    assert drive.calls[FileId("templates")] == 1
    assert all(template.category_path[0].startswith("Category ") for template in catalog.templates)


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
