from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import time
from typing import Protocol, override
import uuid

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    GoogleTokenUnavailableError,
)
from app.cache.service import CacheService
from app.config import settings
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import (
    DocumentGatewayError,
    DocumentTransformError,
    ModelUnavailableError,
    UnsupportedSourceError,
)
from app.document_transform.executor import PIPELINE_VERSION, DocumentTransformExecutor
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.google_presentation_gateway import (
    PPTX_MIME_TYPE,
    GooglePresentationGateway,
)
from app.document_transform.models import PatchGenerator
from app.document_transform.presentation_executor import PresentationTransformExecutor
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    InPlaceCheckpoint,
    OutputStrategy,
    TransformMode,
)
from app.document_transform.slides_brand import presentation_fingerprint
from app.drive.retry import ProviderDeadlineError
from app.drive.schemas import MIME_SLIDE
from app.drive.service import GoogleDriveService
from app.files.errors import FileNotInScopeError, FilesError
from app.files.service import GoogleFilesService
from app.identifiers import FileId, TaskId
from app.models.user import User
from app.tasks.errors import TaskExecutionDeferredError, TaskNotClaimableError
from app.tasks.schemas import (
    CancelledTaskRead,
    DocumentTransformTaskRecord,
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


class ClaimedTaskStore(RetryTaskStore, Protocol):
    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None: ...

    async def succeed(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        result: DocumentTransformResult,
    ) -> TaskRead: ...


class DocumentTransformRunner(Protocol):
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> DocumentTransformResult: ...


class DocumentTransformTaskStore(ClaimedTaskStore, Protocol):
    async def load_document_transform(
        self,
        task_id: TaskId,
    ) -> DocumentTransformTaskRecord: ...

    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim: ...


class QueuedDocumentTransformTaskExecutor(DocumentTransformTaskExecutor):
    def __init__(
        self,
        *,
        tasks: DocumentTransformTaskStore,
        transform: DocumentTransformRunner,
    ) -> None:
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
            checkpoint=record.checkpoint,
        )


@dataclass(frozen=True, slots=True)
class DocumentTransformRuntime:
    transform: DocumentTransformRunner


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
            claimed_record = await self._tasks.load_document_transform(task_id)
            if (
                claimed_record.payload.output_strategy is OutputStrategy.IN_PLACE
                and not settings.DOCUMENT_TRANSFORM_IN_PLACE_ENABLED
            ):
                return await self._tasks.fail(
                    task_id,
                    lease_token=claim.lease_token,
                    error_code="in_place_disabled",
                    error_detail="In-place document transformation is disabled",
                )
            runtime = await self._runtime(
                claimed_record.owner_user_id,
                claimed_record.payload,
                task_id=task_id,
                lease_token=claim.lease_token,
                checkpoint=claimed_record.checkpoint,
                checkpoint_generation=claimed_record.checkpoint_generation,
            )
        except GoogleReauthenticationRequiredError, GoogleTokenUnavailableError:
            return await self._tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code="google_auth_required",
                error_detail="Reconnect Google Drive, then start the document polish again",
            )
        except DocumentGatewayError as exc:
            return await defer_or_fail_retryable_task(
                task_id,
                tasks=self._tasks,
                claim=claim,
                error_code=exc.code,
                error_detail=exc.public_detail,
                cause=exc,
            )
        except DocumentTransformError as exc:
            return await self._tasks.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code=exc.code,
                error_detail=exc.public_detail,
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
            checkpoint=claimed_record.checkpoint,
        )

    async def _runtime(
        self,
        owner_user_id: uuid.UUID,
        payload: DocumentTransformRequest,
        *,
        task_id: TaskId,
        lease_token: uuid.UUID,
        checkpoint: InPlaceCheckpoint | None,
        checkpoint_generation: int,
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
        if isinstance(payload.source, DriveFileSource):
            try:
                source_file = await files.get(payload.source.file_id)
            except FileNotInScopeError as exc:
                raise UnsupportedSourceError from exc
            except FilesError as exc:
                if exc.http_status in {401, 403}:
                    raise UnsupportedSourceError from exc
                raise DocumentGatewayError from exc
            if source_file.mime_type in {MIME_SLIDE, PPTX_MIME_TYPE}:
                presentation_gateway = GooglePresentationGateway(
                    user=prepared.user,
                    files=files,
                    refresh_handler=prepared.refresh_handler,
                    fingerprint=presentation_fingerprint,
                )
                return DocumentTransformRuntime(
                    transform=PresentationTransformExecutor(
                        gateway=presentation_gateway,
                        generator=self._generator,
                        logo_uri=logo_uri,
                    )
                )
        document_gateway = GoogleDocumentGateway(
            user=prepared.user,
            files=files,
            logo_uri=logo_uri,
            refresh_handler=prepared.refresh_handler,
        )
        return DocumentTransformRuntime(
            transform=DocumentTransformExecutor(
                gateway=document_gateway,
                generator=self._generator,
                logo_bytes=logo_bytes,
                checkpoint_store=self._tasks,
                task_id=task_id,
                lease_token=lease_token,
                checkpoint=checkpoint,
                checkpoint_generation=checkpoint_generation,
            )
        )

    async def _load_logo(self) -> bytes | None:
        if self._logo_uri != CURRENT_BRAND_MANIFEST.assets.logo_uri:
            return None
        try:
            response = await self._http.get(self._logo_uri)
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        content = response.content
        if (
            not content.startswith(b"\x89PNG\r\n\x1a\n")
            or len(content) > _MAX_LOGO_BYTES
            or hashlib.sha256(content).hexdigest() != CURRENT_BRAND_MANIFEST.assets.logo_sha256
        ):
            return None
        return content


async def _execute_claimed(
    task_id: TaskId,
    *,
    tasks: ClaimedTaskStore,
    transform: DocumentTransformRunner,
    claim: TaskClaim,
    payload: DocumentTransformRequest,
    checkpoint: InPlaceCheckpoint | None = None,
) -> TaskRead:
    started_at = time.perf_counter()
    source_kind = payload.source.kind
    logger.info(
        "document transform started task_id={task_id} attempt={attempt} source_kind={source_kind} "
        "mode={mode} output_strategy={output_strategy} checkpoint_stage={checkpoint_stage} "
        "mutation_sha256={mutation_sha256} pipeline={pipeline} brand_profile={brand_profile}",
        task_id=task_id,
        attempt=claim.task.attempt_count,
        source_kind=source_kind,
        mode=payload.mode.value,
        output_strategy=payload.output_strategy.value,
        checkpoint_stage=checkpoint.stage.value if checkpoint is not None else "none",
        mutation_sha256=(
            checkpoint.mutation_sha256
            if checkpoint is not None and checkpoint.mutation_sha256 is not None
            else "none"
        ),
        pipeline=PIPELINE_VERSION,
        brand_profile=CURRENT_BRAND_MANIFEST.profile_id,
    )
    try:
        result = await transform.execute(operation_id=task_id, request=payload)
    except ProviderDeadlineError as exc:
        _log_transform_failure(
            task_id=task_id,
            claim=claim,
            payload=payload,
            error_code=DocumentGatewayError.code,
            started_at=started_at,
        )
        return await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=claim,
            error_code=DocumentGatewayError.code,
            error_detail=DocumentGatewayError.public_detail,
            cause=exc,
        )
    except (DocumentGatewayError, ModelUnavailableError) as exc:
        _log_transform_failure(
            task_id=task_id,
            claim=claim,
            payload=payload,
            error_code=exc.code,
            started_at=started_at,
        )
        return await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=claim,
            error_code=exc.code,
            error_detail=exc.public_detail,
            cause=exc,
        )
    except DocumentTransformError as exc:
        _log_transform_failure(
            task_id=task_id,
            claim=claim,
            payload=payload,
            error_code=exc.code,
            started_at=started_at,
        )
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
    completed = await tasks.succeed(
        task_id,
        lease_token=claim.lease_token,
        result=result,
    )
    logger.info(
        "document transform succeeded task_id={task_id} attempt={attempt} source_kind={source_kind} "
        "mode={mode} output_strategy={output_strategy} checkpoint_stage={checkpoint_stage} "
        "mutation_sha256=none pipeline={pipeline} brand_profile={brand_profile} "
        "llm_invoked={llm_invoked} "
        "deterministic_patches={deterministic_patches} model_patches={model_patches} "
        "findings={findings} duration_ms={duration_ms}",
        task_id=task_id,
        attempt=claim.task.attempt_count,
        source_kind=source_kind,
        mode=payload.mode.value,
        output_strategy=payload.output_strategy.value,
        checkpoint_stage=("complete" if result.output_strategy.value == "in_place" else "none"),
        pipeline=result.pipeline_version,
        brand_profile=result.brand_profile_id,
        llm_invoked=result.llm_invoked,
        deterministic_patches=result.deterministic_patch_count,
        model_patches=result.model_patch_count,
        findings=sorted({finding.code for finding in result.findings}),
        duration_ms=round((time.perf_counter() - started_at) * 1000),
    )
    return completed


def _log_transform_failure(
    *,
    task_id: TaskId,
    claim: TaskClaim,
    payload: DocumentTransformRequest,
    error_code: str,
    started_at: float,
) -> None:
    logger.warning(
        "document transform interrupted task_id={task_id} attempt={attempt} source_kind={source_kind} "
        "mode={mode} output_strategy={output_strategy} brand_profile={brand_profile} "
        "error_code={error_code} duration_ms={duration_ms}",
        task_id=task_id,
        attempt=claim.task.attempt_count,
        source_kind=payload.source.kind,
        mode=payload.mode.value,
        output_strategy=payload.output_strategy.value,
        brand_profile=CURRENT_BRAND_MANIFEST.profile_id,
        error_code=error_code,
        duration_ms=round((time.perf_counter() - started_at) * 1000),
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
