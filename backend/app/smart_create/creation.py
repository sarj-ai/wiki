from __future__ import annotations

from abc import ABC, abstractmethod
from typing import override
import uuid

from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.errors import AliasError
from app.aliases.service import AliasService
from app.drive.schemas import MIME_DOC, MIME_SLIDE
from app.extras.schemas import DocIconWrite
from app.extras.service import ExtrasService
from app.files.errors import DriveUpstreamError, FilesError
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import TaskId
from app.smart_create.errors import TemplateChangedError, TemplateUnavailableError
from app.smart_create.schemas import (
    BlankSource,
    SmartCreateResult,
    SmartCreateWarning,
    SmartFileCreate,
    TemplateSource,
)
from app.tasks.service import TaskService
from app.templates.schemas import TemplateRead
from app.templates.service import TemplateCatalogService


class SmartFileCreationService(ABC):
    @abstractmethod
    async def execute(
        self,
        *,
        task_id: TaskId,
        lease_token: uuid.UUID,
        payload: SmartFileCreate,
        existing_resource: FileResponse | None,
    ) -> SmartCreateResult: ...


class DriveSmartFileCreationService(SmartFileCreationService):
    def __init__(
        self,
        *,
        files: FilesService,
        aliases: AliasService,
        extras: ExtrasService,
        tasks: TaskService,
        templates: TemplateCatalogService,
        db: AsyncSession,
    ) -> None:
        self._files = files
        self._aliases = aliases
        self._extras = extras
        self._tasks = tasks
        self._templates = templates
        self._db = db

    @override
    async def execute(
        self,
        *,
        task_id: TaskId,
        lease_token: uuid.UUID,
        payload: SmartFileCreate,
        existing_resource: FileResponse | None,
    ) -> SmartCreateResult:
        destination = await self._files.get(payload.parent_folder_id)
        template = await self._validate_template(payload.source, destination)
        file = existing_resource or await self._recover(task_id, destination)
        if file is None:
            try:
                file = await self._create_file(task_id, payload, template)
            except DriveUpstreamError:
                file = await self._recover(task_id, destination)
                if file is None:
                    raise
            await self._tasks.record_resource(
                task_id,
                lease_token=lease_token,
                resource_id=file.id,
            )

        warnings = await self._enrich(file, payload)
        result = SmartCreateResult(task_id=task_id, file=file, warnings=warnings)
        await self._tasks.succeed(task_id, lease_token=lease_token, result=result)
        return result

    async def _validate_template(
        self,
        source: BlankSource | TemplateSource,
        destination: FileResponse,
    ) -> TemplateRead | None:
        if isinstance(source, BlankSource):
            return None
        template = await self._templates.resolve_template(
            source.template_file_id, source.template_drive_id
        )
        if template is None or destination.drive_id != template.drive_id:
            raise TemplateUnavailableError
        if template.modified_time != source.template_modified_time:
            raise TemplateChangedError
        if template.mime_type != source.mime_type:
            raise TemplateUnavailableError
        return template

    async def _recover(self, task_id: TaskId, destination: FileResponse) -> FileResponse | None:
        if destination.drive_id is None:
            return None
        return await self._files.find_by_task(destination.drive_id, task_id)

    async def _create_file(
        self,
        task_id: TaskId,
        payload: SmartFileCreate,
        template: TemplateRead | None,
    ) -> FileResponse:
        if template is not None:
            return await self._files.copy_template(
                template_file_id=template.file_id,
                parent_folder_id=payload.parent_folder_id,
                name=payload.name,
                task_id=task_id,
                expected_modified_time=template.modified_time,
            )
        source = payload.source
        if not isinstance(source, BlankSource):  # pragma: no cover - validated union invariant
            msg = "Template source must resolve to a Drive template"
            raise TypeError(msg)
        mime_type = MIME_DOC if source.file_type == "document" else MIME_SLIDE
        return await self._files.create_tracked(
            payload.parent_folder_id,
            payload.name,
            mime_type,
            task_id,
        )

    async def _enrich(
        self,
        file: FileResponse,
        payload: SmartFileCreate,
    ) -> list[SmartCreateWarning]:
        warnings: list[SmartCreateWarning] = []
        try:
            await self._aliases.save(file.id, payload.alias)
        except (AliasError, FilesError) as exc:
            detail = exc.public_detail if isinstance(exc, FilesError) else str(exc)
            warnings.append(SmartCreateWarning(code=exc.code, detail=detail))
        except SQLAlchemyError as exc:
            await self._db.rollback()
            logger.warning(
                "Smart Create alias enrichment failed error_type={error_type}",
                error_type=type(exc).__name__,
            )
            warnings.append(
                SmartCreateWarning(
                    code="alias_save_failed",
                    detail="The file was created, but its short link could not be saved",
                )
            )
        try:
            await self._extras.upsert_doc_icon(file.id, DocIconWrite(icon=payload.emoji))
        except SQLAlchemyError as exc:
            await self._db.rollback()
            logger.warning(
                "Smart Create icon enrichment failed error_type={error_type}",
                error_type=type(exc).__name__,
            )
            warnings.append(
                SmartCreateWarning(
                    code="icon_save_failed",
                    detail="The file was created, but its emoji could not be saved",
                )
            )
        return warnings
