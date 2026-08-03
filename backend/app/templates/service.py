from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from datetime import timedelta
from typing import cast, override
from uuid import UUID

from loguru import logger
from pydantic import ValidationError

from app.cache.service import CacheNamespace, CacheService
from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SLIDE, DriveSummary
from app.drive.service import DriveService
from app.identifiers import FileId
from app.sidebar.singleflight import Singleflight
from app.templates.schemas import TemplateCatalogRead, TemplateMimeType, TemplateRead


_TEMPLATES_FOLDER_NAME = "Templates"
_CACHE_TTL = timedelta(seconds=30)
_MAX_VISITED_FOLDERS_PER_DRIVE = 500
_SUPPORTED_MIME_TYPES = frozenset({MIME_DOC, MIME_SLIDE})


_singleflight = Singleflight()


class TemplateCatalogService(ABC):
    @abstractmethod
    async def list_templates(self, *, refresh: bool = False) -> TemplateCatalogRead: ...


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

    async def _discover_and_cache(self) -> TemplateCatalogRead:
        drives = await self._drive.list_drives()
        discovered = await asyncio.gather(
            *(self._discover_drive(drive) for drive in drives),
            return_exceptions=True,
        )
        templates: list[TemplateRead] = []
        for drive, result in zip(drives, discovered, strict=True):
            if isinstance(result, BaseException):
                logger.opt(exception=result).warning(
                    "template discovery failed user={user_id} drive={drive_id}",
                    user_id=self._user_id,
                    drive_id=drive.id,
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
        root_children = await self._drive.list_folder_children(FileId(drive.id))
        template_roots = [
            child
            for child in root_children
            if child.mime_type == MIME_FOLDER and child.name == _TEMPLATES_FOLDER_NAME
        ]
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
        pending: list[tuple[FileId, tuple[str, ...]]] = [(root_id, ())]
        visited: set[FileId] = set()
        templates: list[TemplateRead] = []
        while pending and len(visited) < _MAX_VISITED_FOLDERS_PER_DRIVE:
            folder_id, category = pending.pop(0)
            if folder_id in visited:
                continue
            visited.add(folder_id)
            children = await self._drive.list_folder_children(folder_id)
            for child in children:
                if child.mime_type == MIME_FOLDER:
                    pending.append((child.id, (*category, child.name)))
                elif child.mime_type in _SUPPORTED_MIME_TYPES and child.modified_time is not None:
                    templates.append(
                        TemplateRead(
                            file_id=child.id,
                            drive_id=drive.id,
                            drive_name=drive.name,
                            name=child.name,
                            description=child.description,
                            mime_type=cast("TemplateMimeType", child.mime_type),
                            modified_time=child.modified_time,
                            category_path=list(category),
                        )
                    )
        if pending:
            logger.warning(
                "template discovery folder cap reached user={user_id} drive={drive_id}",
                user_id=self._user_id,
                drive_id=drive.id,
            )
        return templates
