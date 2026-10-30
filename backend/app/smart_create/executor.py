from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import override
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.aliases.service import PostgresAliasService
from app.auth.google_tokens import GoogleTokenManager
from app.cache.service import CacheService
from app.drive.service import GoogleDriveService
from app.extras.service import PostgresExtrasService
from app.files.errors import DriveUpstreamError, FilesError
from app.files.service import GoogleFilesService
from app.identifiers import TaskId
from app.models.user import User
from app.sidebar.cache import PostgresSidebarCacheService, SidebarCacheService
from app.sidebar.singleflight import Singleflight
from app.smart_create.creation import DriveSmartFileCreationService
from app.tasks.errors import TaskExecutionDeferredError, TaskNotClaimableError
from app.tasks.schemas import CancelledTaskRead, FailedTaskRead, SucceededTaskRead, TaskRead
from app.tasks.service import TaskService
from app.templates.service import DriveTemplateCatalogService


_EXECUTION_LEASE = timedelta(minutes=6)


class SmartCreateTaskExecutor(ABC):
    @abstractmethod
    async def execute(self, task_id: TaskId) -> TaskRead: ...


@dataclass(frozen=True, slots=True)
class SmartCreateRuntime:
    creation: DriveSmartFileCreationService
    files: GoogleFilesService
    sidebar: SidebarCacheService


class DriveSmartCreateTaskExecutor(SmartCreateTaskExecutor):
    def __init__(
        self,
        *,
        db: AsyncSession,
        tasks: TaskService,
        cache: CacheService,
        http: httpx.AsyncClient | None = None,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._db = db
        self._tasks = tasks
        self._cache = cache
        self._http = http
        self._sessionmaker = sessionmaker

    @override
    async def execute(self, task_id: TaskId) -> TaskRead:
        record = await self._tasks.load_smart_create(task_id)
        if _is_terminal(record.task):
            return record.task
        try:
            claim = await self._tasks.claim(task_id, lease_duration=_EXECUTION_LEASE)
        except TaskNotClaimableError as exc:
            current = (await self._tasks.load_smart_create(task_id)).task
            if _is_terminal(current):
                return current
            raise TaskExecutionDeferredError from exc

        runtime = await self._runtime(record.owner_user_id)
        try:
            existing = (
                await runtime.files.get(claim.task.resource_id)
                if claim.task.resource_id is not None
                else None
            )
            result = await runtime.creation.execute(
                task_id=task_id,
                lease_token=claim.lease_token,
                payload=record.payload,
                existing_resource=existing,
            )
        except DriveUpstreamError as exc:
            await self._tasks.defer(task_id, lease_token=claim.lease_token)
            raise TaskExecutionDeferredError from exc
        except FilesError as exc:
            return await self._tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code=exc.code,
                error_detail=exc.public_detail,
            )

        if result.file.drive_id is not None:
            await runtime.sidebar.invalidate_drive(self._db, result.file.drive_id)
        return (await self._tasks.load_smart_create(task_id)).task

    async def _runtime(self, owner_user_id: uuid.UUID) -> SmartCreateRuntime:
        user = await self._db.get(User, owner_user_id)
        if user is None:  # pragma: no cover — owner FK cascades task deletion
            msg = "Task owner no longer exists"
            raise RuntimeError(msg)
        if self._http is None:  # pragma: no cover — production dependency always provides it
            msg = "Google token HTTP client is unavailable"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(self._db, self._http).prepare(user)
        drive = GoogleDriveService(
            prepared.user,
            self._cache,
            refresh_handler=prepared.refresh_handler,
        )
        files = GoogleFilesService(user=prepared.user, drive=drive)
        aliases = PostgresAliasService(db=self._db, files=files, user=prepared.user)
        extras = PostgresExtrasService(db=self._db, user=prepared.user)
        templates = DriveTemplateCatalogService(
            drive=drive,
            user_id=prepared.user.id,
            cache=self._cache,
        )
        creation = DriveSmartFileCreationService(
            files=files,
            aliases=aliases,
            extras=extras,
            tasks=self._tasks,
            templates=templates,
            db=self._db,
        )
        sidebar = PostgresSidebarCacheService(
            drive=drive,
            user=prepared.user,
            singleflight=Singleflight(),
            bg_tasks=set(),
            sessionmaker=self._sessionmaker,
        )
        return SmartCreateRuntime(creation=creation, files=files, sidebar=sidebar)


def _is_terminal(task: TaskRead) -> bool:
    return isinstance(task, SucceededTaskRead | FailedTaskRead | CancelledTaskRead)
