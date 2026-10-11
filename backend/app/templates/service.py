from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections import deque
from datetime import timedelta
from functools import partial
from typing import override
from uuid import UUID

from loguru import logger
from pydantic import ValidationError

from app.auth.google_tokens import provider_refresh_scope
from app.cache.service import CacheNamespace, CacheService
from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SLIDE, DriveNode, DriveSummary
from app.drive.service import DriveService
from app.identifiers import DriveId, FileId
from app.sidebar.singleflight import Singleflight
from app.templates.schemas import TemplateCatalogRead, TemplateMimeType, TemplateRead


_TEMPLATES_FOLDER_NAME = "Templates"
_CACHE_TTL = timedelta(seconds=30)
_MAX_VISITED_FOLDERS_PER_DRIVE = 500
_DISCOVERY_CONCURRENCY = 8
_SUPPORTED_MIME_TYPES = frozenset({MIME_DOC, MIME_SLIDE})


_singleflight = Singleflight[TemplateCatalogRead]()
_workspace_singleflight = Singleflight[list[TemplateRead]]()


class TemplateCatalogService(ABC):
    @abstractmethod
    async def list_templates(self, *, refresh: bool = False) -> TemplateCatalogRead: ...

    async def resolve_template(self, file_id: FileId, drive_id: DriveId) -> TemplateRead | None:
        catalog = await self.list_templates(refresh=True)
        return _find_template(catalog.templates, file_id=file_id, drive_id=drive_id)


class DriveTemplateCatalogService(TemplateCatalogService):
    def __init__(
        self,
        *,
        drive: DriveService,
        user_id: UUID,
        cache: CacheService | None = None,
    ) -> None:
        self._drive = drive
        self._user_id = user_id
        self._cache = cache
        self._folder_reads = asyncio.Semaphore(_DISCOVERY_CONCURRENCY)

    @override
    async def list_templates(self, *, refresh: bool = False) -> TemplateCatalogRead:
        if not refresh and self._cache is not None:
            cached = await self._cache.get(CacheNamespace.TEMPLATE_CATALOG, str(self._user_id))
            if cached is not None:
                try:
                    return TemplateCatalogRead.model_validate(cached)
                except ValidationError:
                    await self._cache.delete(CacheNamespace.TEMPLATE_CATALOG, str(self._user_id))
        return await _singleflight.do(str(self._user_id), self._discover_and_cache)

    @override
    async def resolve_template(self, file_id: FileId, drive_id: DriveId) -> TemplateRead | None:
        drives = await self._drive.list_drives()
        drive = next((item for item in drives if item.id == drive_id), None)
        if drive is None:
            return None
        # Copy validation needs a fresh catalog for this workspace. Unrelated
        # workspace trees do not contribute to the selected template verdict.
        (templates,) = await asyncio.gather(
            _workspace_singleflight.do(
                f"{self._user_id}:{drive.id}", partial(self._discover_workspace, drive)
            ),
            return_exceptions=True,
        )
        if isinstance(templates, BaseException):
            logger.warning(
                "template discovery failed error_type={error_type}",
                error_type=type(templates).__name__,
            )
            return None
        return _find_template(templates, file_id=file_id, drive_id=drive_id)

    async def _discover_workspace(self, drive: DriveSummary) -> list[TemplateRead]:
        # Credential refresh belongs to the discovery task that owns the
        # provider reads and their concurrency bound.
        with provider_refresh_scope():
            return await self._discover_drive(drive)

    async def _discover_and_cache(self) -> TemplateCatalogRead:
        drives = await self._drive.list_drives()
        discovered = await asyncio.gather(
            *(self._discover_drive(drive) for drive in drives),
            return_exceptions=True,
        )
        templates: list[TemplateRead] = []
        for _drive, result in zip(drives, discovered, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "template discovery failed error_type={error_type}",
                    error_type=type(result).__name__,
                )
                continue
            templates.extend(result)
        templates.sort(
            key=lambda item: (
                item.drive_name.casefold(),
                tuple(part.casefold() for part in item.category_path),
                item.name.casefold(),
                item.file_id,
            )
        )
        catalog = TemplateCatalogRead(templates=templates)
        if self._cache is not None:
            await self._cache.put(
                CacheNamespace.TEMPLATE_CATALOG,
                str(self._user_id),
                catalog.model_dump(mode="json"),
                ttl=_CACHE_TTL,
            )
        return catalog

    async def _discover_drive(self, drive: DriveSummary) -> list[TemplateRead]:
        async with self._folder_reads:
            template_roots = await self._drive.list_named_root_folders(
                drive.id, name=_TEMPLATES_FOLDER_NAME
            )
        discovered = await asyncio.gather(
            *(
                self._walk_template_root(drive, root.id)
                for root in sorted(template_roots, key=lambda item: item.id)
            )
        )
        return [template for root_templates in discovered for template in root_templates]

    async def _walk_template_root(
        self,
        drive: DriveSummary,
        root_id: FileId,
    ) -> list[TemplateRead]:
        pending: deque[tuple[FileId, tuple[str, ...]]] = deque([(root_id, ())])
        visited: set[FileId] = set()
        templates: list[TemplateRead] = []
        while pending and len(visited) < _MAX_VISITED_FOLDERS_PER_DRIVE:
            batch = _next_template_batch(pending, visited)
            groups = (
                [await self._list_folder_children(batch[0][0])]
                if len(batch) == 1
                else await asyncio.gather(
                    *(self._list_folder_children(folder_id) for folder_id, _category in batch)
                )
            )
            for (_folder_id, category), children in zip(batch, groups, strict=True):
                for child in children:
                    if child.mime_type == MIME_FOLDER:
                        pending.append((child.id, (*category, child.name)))
                    elif (
                        child.mime_type in _SUPPORTED_MIME_TYPES and child.modified_time is not None
                    ):
                        templates.append(
                            TemplateRead(
                                file_id=child.id,
                                drive_id=drive.id,
                                drive_name=drive.name,
                                name=child.name,
                                description=child.description,
                                mime_type=_template_mime_type(child.mime_type),
                                modified_time=child.modified_time,
                                category_path=list(category),
                            )
                        )
        if pending:
            logger.warning("template discovery folder cap reached")
        return templates

    async def _list_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        async with self._folder_reads:
            return await self._drive.list_template_children(folder_id)


def _next_template_batch(
    pending: deque[tuple[FileId, tuple[str, ...]]], visited: set[FileId]
) -> list[tuple[FileId, tuple[str, ...]]]:
    batch: list[tuple[FileId, tuple[str, ...]]] = []
    while (
        pending
        and len(batch) < _DISCOVERY_CONCURRENCY
        and len(visited) < _MAX_VISITED_FOLDERS_PER_DRIVE
    ):
        item = pending.popleft()
        if item[0] not in visited:
            visited.add(item[0])
            batch.append(item)
    return batch


def _template_mime_type(value: str) -> TemplateMimeType:
    match value:
        case "application/vnd.google-apps.document":
            return "application/vnd.google-apps.document"
        case "application/vnd.google-apps.presentation":
            return "application/vnd.google-apps.presentation"
        case _:
            msg = f"Unsupported template MIME type: {value}"
            raise ValueError(msg)


def _find_template(
    templates: list[TemplateRead], *, file_id: FileId, drive_id: DriveId
) -> TemplateRead | None:
    return next(
        (item for item in templates if item.file_id == file_id and item.drive_id == drive_id),
        None,
    )
