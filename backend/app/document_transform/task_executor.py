from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol, override
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    GoogleTokenUnavailableError,
)
from app.cache.service import CacheService
from app.document_transform.errors import (
    DocumentGatewayError,
    DocumentTransformError,
    ModelUnavailableError,
)
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.models import PatchGenerator
from app.document_transform.schemas import (
    DocumentTransformRequest,
    TransformMode,
)
from app.drive.service import GoogleDriveService
from app.files.service import GoogleFilesService
from app.identifiers import TaskId
from app.models.user import User
from app.tasks.errors import TaskExecutionDeferredError, TaskNotClaimableError
from app.tasks.schemas import (
    CancelledTaskRead,
    FailedTaskRead,
    SucceededTaskRead,
    TaskClaim,
    TaskRead,
)
from app.tasks.service import TaskService


_EXECUTION_LEASE = timedelta(minutes=6)
_MAX_LOGO_BYTES = 2 * 1024 * 1024
_MAX_TASK_ATTEMPTS = 10


class DocumentTransformTaskExecutor(ABC):
    @abstractmethod
    async def execute(self, task_id: TaskId) -> TaskRead: ...


class RetryTaskStore(Protocol):
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead: ...

    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead: ...


class QueuedDocumentTransformTaskExecutor(DocumentTransformTaskExecutor):
    def __init__(self, *, tasks: TaskService, transform: DocumentTransformExecutor) -> None:
        self._tasks = tasks
        self._transform = transform

    @override
    async def execute(self, task_id: TaskId) -> TaskRead:
        record = await self._tasks.load_document_transform(task_id)
        if isinstance(record.task, SucceededTaskRead | FailedTaskRead | CancelledTaskRead):
            return record.task
        try:
            claim = await self._tasks.claim(task_id, lease_duration=_EXECUTION_LEASE)
        except TaskNotClaimableError as exc:
            raise TaskExecutionDeferredError from exc
        return await _execute_claimed(
            task_id,
            tasks=self._tasks,
            transform=self._transform,
            claim=claim,
            payload=record.payload,
        )


@dataclass(frozen=True, slots=True)
class DocumentTransformRuntime:
    transform: DocumentTransformExecutor


class DriveDocumentTransformTaskExecutor(DocumentTransformTaskExecutor):
    def __init__(
        self,
        *,
        db: AsyncSession,
        tasks: TaskService,
        cache: CacheService,
        http: httpx.AsyncClient,
        generator: PatchGenerator,
        logo_uri: str,
    ) -> None:
        self._db = db
        self._tasks = tasks
        self._cache = cache
        self._http = http
        self._generator = generator
        self._logo_uri = logo_uri

    @override
    async def execute(self, task_id: TaskId) -> TaskRead:
        record = await self._tasks.load_document_transform(task_id)
        if isinstance(record.task, SucceededTaskRead | FailedTaskRead | CancelledTaskRead):
            return record.task
        try:
            claim = await self._tasks.claim(task_id, lease_duration=_EXECUTION_LEASE)
        except TaskNotClaimableError as exc:
            raise TaskExecutionDeferredError from exc
        try:
            runtime = await self._runtime(record.owner_user_id, record.payload)
        except GoogleReauthenticationRequiredError, GoogleTokenUnavailableError:
            return await self._tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code="google_auth_required",
                error_detail="Reconnect Google Drive, then start the document polish again",
            )
        except (RuntimeError, TypeError, ValueError) as exc:
            return await defer_or_fail_retryable_task(
                task_id,
                tasks=self._tasks,
                claim=claim,
                error_code="document_transform_initialization_failed",
                error_detail="The document transformation could not be initialized",
                cause=exc,
            )
        return await _execute_claimed(
            tasks=self._tasks,
            transform=runtime.transform,
            task_id=task_id,
            claim=claim,
            payload=record.payload,
        )

    async def _runtime(
        self,
        owner_user_id: uuid.UUID,
        payload: DocumentTransformRequest,
    ) -> DocumentTransformRuntime:
        user = await self._db.get(User, owner_user_id)
        if user is None:  # pragma: no cover - owner FK cascades task deletion
            msg = "Task owner no longer exists"
            raise RuntimeError(msg)
        prepared = await GoogleTokenManager(self._db, self._http).prepare(user)
        drive = GoogleDriveService(
            prepared.user,
            self._cache,
            refresh_handler=prepared.refresh_handler,
        )
        files = GoogleFilesService(user=prepared.user, drive=drive)
        logo_bytes: bytes | None = None
        logo_uri = self._logo_uri
        if payload.mode is not TransformMode.UNSLOP:
            logo_bytes = await self._load_logo()
            logo_uri = self._logo_uri if logo_bytes is not None else ""
        gateway = GoogleDocumentGateway(
            user=prepared.user,
            files=files,
            logo_uri=logo_uri,
            refresh_handler=prepared.refresh_handler,
        )
        return DocumentTransformRuntime(
            transform=DocumentTransformExecutor(
                gateway=gateway,
                generator=self._generator,
                logo_bytes=logo_bytes,
            )
        )

    async def _load_logo(self) -> bytes | None:
        try:
            response = await self._http.get(self._logo_uri)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        content = response.content
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > _MAX_LOGO_BYTES:
            return None
        return content


async def _execute_claimed(
    task_id: TaskId,
    *,
    tasks: TaskService,
    transform: DocumentTransformExecutor,
    claim: TaskClaim,
    payload: DocumentTransformRequest,
) -> TaskRead:
    try:
        result = await transform.execute(operation_id=task_id, request=payload)
    except (DocumentGatewayError, ModelUnavailableError) as exc:
        return await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=claim,
            error_code=exc.code,
            error_detail=exc.public_detail,
            cause=exc,
        )
    except DocumentTransformError as exc:
        return await tasks.fail(
            task_id,
            lease_token=claim.lease_token,
            error_code=exc.code,
            error_detail=exc.public_detail,
        )
    await tasks.record_resource(
        task_id,
        lease_token=claim.lease_token,
        resource_id=result.output.file_id,
    )
    return await tasks.succeed(
        task_id,
        lease_token=claim.lease_token,
        result=result,
    )


async def defer_or_fail_retryable_task(
    task_id: TaskId,
    *,
    tasks: RetryTaskStore,
    claim: TaskClaim,
    error_code: str,
    error_detail: str,
    cause: Exception,
) -> TaskRead:
    if claim.task.attempt_count >= _MAX_TASK_ATTEMPTS:
        return await tasks.fail(
            task_id,
            lease_token=claim.lease_token,
            error_code=error_code,
            error_detail=error_detail,
        )
    await tasks.defer(task_id, lease_token=claim.lease_token)
    raise TaskExecutionDeferredError from cause
