from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from typing import override
import uuid

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, JsonValue, RootModel
from sqlalchemy import Text, and_, case, cast, delete, func, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.sql.selectable import CompoundSelect

from app.auth.encryption import decrypt_cache, encrypt_cache
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import CheckpointTooLargeError, OutputConflictError
from app.document_transform.schemas import (
    CopyCheckpoint,
    CopyCheckpointStage,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    InPlaceCheckpoint,
    MarkdownSource,
    OutputStrategy,
    QaCopyPermissionEvidence,
    QaDocumentTransformRequest,
    QaDocumentTransformResult,
    TransformMode,
    WordCopyCheckpoint,
)
from app.document_transform.versions import CURRENT_PIPELINE_VERSIONS, DOCUMENT_PIPELINE_VERSION
from app.identifiers import DocsRevisionId, FileId, IdempotencyKey, TaskId, UserId
from app.models.task import Task
from app.models.task_replay_key import TaskReplayKey
from app.models.wiki import DocumentHeaderProvenance
from app.tasks.errors import (
    TaskAdmissionLimitError,
    TaskIdempotencyConflictError,
    TaskLeaseLostError,
    TaskNotClaimableError,
    TaskNotFoundError,
    TaskTargetBusyError,
)
from app.tasks.schemas import (
    DocumentTransformAdmission,
    DocumentTransformTaskRecord,
    RunningTaskRead,
    SmartCreateTaskRecord,
    SucceededTaskRead,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
    TaskStatus,
)


class _TaskJsonObject(RootModel[dict[str, JsonValue]]):
    """Validate serialized task payloads without changing their JSON shape."""


class _TaskReadPayload(RootModel[TaskRead]):
    """Validate the existing status-discriminated task union."""


_DEFAULT_RETENTION = timedelta(days=7)
_MAX_ERROR_DETAIL_LENGTH = 256
_TASK_FAILURE_DETAIL = "Task could not be completed"
_MAX_CHECKPOINT_PLAN_BYTES = 2 * 1024 * 1024
_CHECKPOINT_PLAN_FIELDS = frozenset(
    {"source_title", "requests", "replacements", "findings", "headers"}
)
_MAX_ACTIVE_DOCUMENT_TRANSFORMS_PER_OWNER = 20
_MAX_ACTIVE_IN_PLACE_TASKS_PER_OWNER = 10
_MARKDOWN_CIPHERTEXT_FIELD = "content_ciphertext"
_MARKDOWN_SHA256_FIELD = "content_sha256"
_MARKDOWN_ENCRYPTION_FIELD = "content_encryption"
_MARKDOWN_ENCRYPTION_VERSION = "fernet-v1"
_TERMINAL_REDACTED_PAYLOAD: dict[str, JsonValue] = {"redacted": True}
_LEASE_RETENTION_GRACE = timedelta(minutes=10)
_COPY_CHECKPOINT_CIPHERTEXT_FIELD = "checkpoint_ciphertext"
_MAX_DISPATCH_RECONCILIATION_BATCH = 100
_OWNER_LOCK_RETRY_DELAY = 0.01
_MAX_OWNER_LOCK_RETRY_DELAY = 0.2
_OWNER_LOCK_TIMEOUT_SECONDS = 120
_PUBLIC_TASK_COLUMNS = (
    Task.id,
    Task.kind,
    Task.status,
    Task.result,
    Task.resource_id,
    Task.error_code,
    Task.error_detail,
    Task.attempt_count,
    Task.started_at,
    Task.finished_at,
    Task.created_at,
)
_PUBLIC_TASK_LOAD = load_only(*_PUBLIC_TASK_COLUMNS, raiseload=True)
_REPLAY_TASK_LOAD = load_only(*_PUBLIC_TASK_COLUMNS, Task.request_hash, raiseload=True)


class TaskService(ABC):
    async def get_current_in_place_result(
        self,
        owner_user_id: UserId,
        file_id: FileId,
        mode: TransformMode,
        revision_id: DocsRevisionId,
    ) -> DocumentTransformResult | None:
        raise NotImplementedError

    async def get_document_transform_admission(
        self, owner_user_id: uuid.UUID, idempotency_key: IdempotencyKey
    ) -> DocumentTransformAdmission:
        raise NotImplementedError

    async def pending_document_transform_dispatches(self, *, limit: int = 50) -> list[TaskId]:
        raise NotImplementedError

    @abstractmethod
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
        admit: Callable[[], Awaitable[None]] | None = None,
        admit_new: Callable[[], None] | None = None,
        admit_terminal_replay: Callable[[TaskRead], Awaitable[bool]] | None = None,
    ) -> TaskReservation: ...

    @abstractmethod
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim: ...

    @abstractmethod
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead: ...

    @abstractmethod
    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None: ...

    @abstractmethod
    async def succeed(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        result: BaseModel,
    ) -> TaskRead: ...

    @abstractmethod
    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead: ...

    @abstractmethod
    async def discard_pending(self, task_id: TaskId, *, owner_user_id: UserId) -> bool: ...

    @abstractmethod
    async def cancel(self, task_id: TaskId, *, owner_user_id: UserId) -> TaskRead: ...

    @abstractmethod
    async def get_for_owner(self, task_id: TaskId, owner_user_id: UserId) -> TaskRead: ...

    async def get_for_owner_bounded_result(
        self,
        task_id: TaskId,
        owner_user_id: UserId,
        *,
        max_result_bytes: int,
        max_findings: int,
        max_model_entries: int,
        max_tool_entries: int,
    ) -> TaskRead:
        raise NotImplementedError

    async def get_qa_permission_evidence(
        self,
        task_id: TaskId,
        owner_user_id: UserId,
        *,
        max_evidence_bytes: int,
    ) -> QaCopyPermissionEvidence:
        raise NotImplementedError

    @abstractmethod
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord: ...

    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord:
        raise NotImplementedError

    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int:
        raise NotImplementedError


class PostgresTaskService(TaskService):
    def __init__(self, *, db: AsyncSession) -> None:
        self._db = db

    @override
    async def get_current_in_place_result(
        self,
        owner_user_id: UserId,
        file_id: FileId,
        mode: TransformMode,
        revision_id: DocsRevisionId,
    ) -> DocumentTransformResult | None:
        row = (
            await self._db.execute(
                select(Task)
                .options(_PUBLIC_TASK_LOAD)
                .where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                    Task.status == TaskStatus.SUCCEEDED,
                    Task.in_place_target == file_id,
                    Task.finished_at.is_not(None),
                    Task.expires_at > func.now(),
                    Task.result["output_strategy"].astext == OutputStrategy.IN_PLACE.value,
                    Task.result["mode"].astext == mode.value,
                    Task.result["output"]["file_id"].astext == file_id,
                    Task.result["pipeline_version"].astext == DOCUMENT_PIPELINE_VERSION,
                    Task.result["brand_profile_sha256"].astext == CURRENT_BRAND_MANIFEST.sha256,
                )
                .order_by(Task.finished_at.desc(), Task.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        task = _to_read(row)
        if not isinstance(task, SucceededTaskRead) or not isinstance(
            task.result, DocumentTransformResult
        ):
            return None
        return task.result if task.result.output_revision_id == revision_id else None

    @override
    async def pending_document_transform_dispatches(self, *, limit: int = 50) -> list[TaskId]:
        if not 1 <= limit <= _MAX_DISPATCH_RECONCILIATION_BATCH:
            msg = "Dispatch reconciliation limit must be between 1 and 100"
            raise ValueError(msg)
        statement = (
            select(Task.id)
            .where(
                Task.kind == literal(TaskKind.FILE_DOCUMENT_TRANSFORM.value, literal_execute=True),
                Task.status == literal(TaskStatus.PENDING.value, literal_execute=True),
                Task.dispatch_ready_at <= func.now() - timedelta(minutes=2),
                Task.expires_at > func.now(),
            )
            .order_by(Task.dispatch_ready_at, Task.id)
            .limit(limit)
        )
        return list((await self._db.execute(statement)).scalars().all())

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
        admit: Callable[[], Awaitable[None]] | None = None,
        admit_new: Callable[[], None] | None = None,
        admit_terminal_replay: Callable[[TaskRead], Awaitable[bool]] | None = None,
    ) -> TaskReservation:
        serialized = _serialize(payload)
        request_hash = _request_hash(serialized)
        document_transform_payload = (
            payload
            if kind is TaskKind.FILE_DOCUMENT_TRANSFORM
            and isinstance(payload, DocumentTransformRequest)
            else None
        )
        in_place_source = (
            document_transform_payload.source
            if document_transform_payload is not None
            and document_transform_payload.output_strategy is OutputStrategy.IN_PLACE
            and isinstance(document_transform_payload.source, DriveFileSource)
            else None
        )
        if document_transform_payload is not None:
            replay = await self._admit_document_transform(
                owner_user_id=owner_user_id,
                idempotency_key=idempotency_key,
                payload=document_transform_payload,
                request_hash=request_hash,
                admit=admit,
                admit_new=admit_new,
                admit_terminal_replay=admit_terminal_replay,
            )
            if replay is not None:
                return replay
        # Non-document tasks do not take the document owner lock or participate
        # in semantic replay, so their admission still runs here.
        if admit is not None and document_transform_payload is None:
            try:
                await admit()
            except BaseException:
                await self._db.rollback()
                raise
        now = datetime.now(UTC)
        expired = (
            delete(Task)
            .where(
                Task.expires_at <= func.now(),
                or_(
                    Task.status != TaskStatus.RUNNING,
                    Task.lease_expires_at <= func.now() - _LEASE_RETENTION_GRACE,
                ),
            )
            .cte("delete_expired_tasks")
        )
        statement = (
            pg_insert(Task)
            .values(
                owner_user_id=owner_user_id,
                kind=kind,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                payload=_payload_for_storage(
                    kind=kind,
                    payload=payload,
                    serialized=serialized,
                ),
                dispatch_ready_at=now if admit is not None else None,
                in_place_target=(in_place_source.file_id if in_place_source else None),
                expires_at=now + _DEFAULT_RETENTION,
            )
            .on_conflict_do_nothing()
            .add_cte(expired)
            .returning(Task)
            .options(_PUBLIC_TASK_LOAD)
        )
        task = (await self._db.execute(statement)).scalar_one_or_none()
        if task is not None:
            await self._db.commit()
            return TaskReservation(task=_to_read(task), created=True)

        existing = await self._maybe_existing(owner_user_id, kind, idempotency_key)
        if existing is None and document_transform_payload is not None:
            # PostgreSQL may test the original key's uniqueness before the
            # unreferenced expiry DELETE CTE runs. The owner lock is still held;
            # retry once against this transaction's completed cleanup, without
            # repeating admission or changing the insertion intent.
            task = (await self._db.execute(statement)).scalar_one_or_none()
            if task is not None:
                await self._db.commit()
                return TaskReservation(task=_to_read(task), created=True)
        if existing is None:
            await self._db.rollback()
            raise TaskTargetBusyError
        if existing.request_hash != request_hash:
            raise TaskIdempotencyConflictError
        return TaskReservation(task=_to_read(existing), created=False)

    async def _admit_document_transform(
        self,
        *,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: DocumentTransformRequest,
        request_hash: str,
        admit: Callable[[], Awaitable[None]] | None,
        admit_new: Callable[[], None] | None,
        admit_terminal_replay: Callable[[TaskRead], Awaitable[bool]] | None,
    ) -> TaskReservation | None:
        kind = TaskKind.FILE_DOCUMENT_TRANSFORM
        await self._lock_document_transform_owner(owner_user_id)
        existing = await self._maybe_existing(owner_user_id, kind, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                await self._db.rollback()
                raise TaskIdempotencyConflictError
            await self._db.commit()
            return TaskReservation(task=_to_read(existing), created=False)
        active_row = await self._find_active_transform(owner_user_id, payload, request_hash)
        if active_row is not None:
            replay = await self._reuse_active_transform(
                active_row=active_row,
                owner_user_id=owner_user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                admit=admit,
            )
            if replay is not None:
                return replay
        else:
            # Every fresh key requires source and destination authorization.
            try:
                if admit is not None:
                    await admit()
            except BaseException:
                await self._db.rollback()
                raise
        terminal_replay = await self._maybe_retained_terminal_replay(
            owner_user_id=owner_user_id,
            kind=kind,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            admit=admit_terminal_replay,
        )
        if terminal_replay is not None:
            return terminal_replay
        try:
            if admit_new is not None:
                admit_new()
        except BaseException:
            await self._db.rollback()
            raise
        await self._check_document_transform_limits(owner_user_id, payload)
        return None

    async def _lock_document_transform_owner(
        self,
        owner_user_id: uuid.UUID,
        *,
        timeout_seconds: float = _OWNER_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        try:
            async with asyncio.timeout(timeout_seconds):
                await self._wait_for_document_transform_owner(owner_user_id)
        except BaseException:
            await self._db.rollback()
            raise

    async def _wait_for_document_transform_owner(self, owner_user_id: uuid.UUID) -> None:
        delay = _OWNER_LOCK_RETRY_DELAY
        while True:
            acquired = (
                await self._db.execute(
                    select(
                        func.pg_try_advisory_xact_lock(func.hashtextextended(str(owner_user_id), 0))
                    )
                )
            ).scalar_one()
            if not isinstance(acquired, bool):
                msg = "Document-transform owner lock returned an invalid value"
                raise TypeError(msg)
            if acquired:
                return
            pending_writes = bool(self._db.new or self._db.dirty or self._db.deleted)
            if not pending_writes:
                transaction_id = (
                    await self._db.execute(select(func.pg_current_xact_id_if_assigned()))
                ).scalar_one()
                pending_writes = transaction_id is not None
            if pending_writes:
                # Caller writes, including already-flushed/Core writes,
                # must remain atomic with admission. Keep their transaction.
                await self._db.execute(
                    select(func.pg_advisory_xact_lock(func.hashtextextended(str(owner_user_id), 0)))
                )
                return
            # Read-only waiters must not consume the pool while the owner
            # performs checks that need an independent cache connection.
            await self._db.commit()
            jitter = 0.5 + uuid.uuid7().bytes[-1] / 510
            await asyncio.sleep(delay * jitter)
            delay = min(delay * 2, _MAX_OWNER_LOCK_RETRY_DELAY)

    async def _find_active_transform(
        self,
        owner_user_id: uuid.UUID,
        payload: DocumentTransformRequest,
        request_hash: str,
    ) -> Row[Task, bool] | None:
        source = payload.source
        source_predicate = (
            and_(
                Task.payload["source"]["kind"].astext == "drive_file",
                Task.payload["source"]["file_id"].astext == source.file_id,
            )
            if isinstance(source, DriveFileSource)
            else and_(
                Task.payload["source"]["kind"].astext == source.kind,
                Task.request_hash == request_hash,
            )
        )
        return (
            await self._db.execute(
                select(Task, (Task.expires_at <= func.now()).label("expired"))
                .options(_REPLAY_TASK_LOAD)
                .where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                    Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)),
                    or_(
                        Task.expires_at > func.now(),
                        and_(
                            Task.status == TaskStatus.RUNNING,
                            Task.lease_expires_at > func.now() - _LEASE_RETENTION_GRACE,
                        ),
                    ),
                    source_predicate,
                )
                .order_by(Task.created_at, Task.id)
                .limit(1)
            )
        ).one_or_none()

    async def _reuse_active_transform(
        self,
        *,
        active_row: Row[Task, bool],
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        request_hash: str,
        admit: Callable[[], Awaitable[None]] | None,
    ) -> TaskReservation | None:
        # The provider await can outlive the selected row. Recheck under a row
        # lock before binding the new retry key.
        try:
            if admit is not None:
                await admit()
        except BaseException:
            await self._db.rollback()
            raise
        refreshed = (
            await self._db.execute(
                select(Task, (Task.expires_at <= func.clock_timestamp()).label("expired"))
                .options(_REPLAY_TASK_LOAD)
                .where(
                    Task.id == active_row[0].id,
                    Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)),
                    or_(
                        Task.expires_at > func.clock_timestamp(),
                        and_(
                            Task.status == TaskStatus.RUNNING,
                            Task.lease_expires_at > func.clock_timestamp() - _LEASE_RETENTION_GRACE,
                        ),
                    ),
                )
                .with_for_update()
            )
        ).one_or_none()
        if refreshed is None:
            return None
        if refreshed[1] or refreshed[0].request_hash != request_hash:
            await self._db.rollback()
            raise TaskTargetBusyError
        await self._bind_replay_key(
            owner_user_id=owner_user_id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            idempotency_key=idempotency_key,
            task_id=refreshed[0].id,
        )
        await self._db.commit()
        return TaskReservation(task=_to_read(refreshed[0]), created=False)

    async def _check_document_transform_limits(
        self, owner_user_id: uuid.UUID, payload: DocumentTransformRequest
    ) -> None:
        statement = (
            select(
                func.count().filter(Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM),
                func.count().filter(Task.in_place_target.is_not(None)),
            )
            .select_from(Task)
            .where(
                Task.owner_user_id == owner_user_id,
                Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)),
                Task.expires_at > func.now(),
            )
        )
        if payload.output_strategy is not OutputStrategy.IN_PLACE:
            statement = statement.where(Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM)
        active_counts = (await self._db.execute(statement)).one()
        active_count, active_in_place_count = active_counts
        active_count = _validated_active_count(active_count)
        active_in_place_count = _validated_active_count(active_in_place_count)
        owner_limit_reached = active_count >= _MAX_ACTIVE_DOCUMENT_TRANSFORMS_PER_OWNER
        in_place_limit_reached = (
            payload.output_strategy is OutputStrategy.IN_PLACE
            and active_in_place_count >= _MAX_ACTIVE_IN_PLACE_TASKS_PER_OWNER
        )
        if owner_limit_reached or in_place_limit_reached:
            await self._db.rollback()
            raise TaskAdmissionLimitError

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        if lease_duration <= timedelta(0):
            msg = "lease_duration must be positive"
            raise ValueError(msg)
        now = datetime.now(UTC)
        lease_token = uuid.uuid7()
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.expires_at > func.now(),
                or_(
                    Task.status == TaskStatus.PENDING,
                    and_(
                        Task.status == TaskStatus.RUNNING,
                        Task.lease_expires_at <= func.now(),
                    ),
                ),
            )
            .values(
                status=TaskStatus.RUNNING,
                started_at=now,
                lease_token=lease_token,
                lease_expires_at=now + lease_duration,
                expires_at=func.greatest(
                    Task.expires_at,
                    now + lease_duration + _LEASE_RETENTION_GRACE,
                ),
                attempt_count=Task.attempt_count + 1,
                updated_at=now,
            )
            .returning(Task)
            .options(_PUBLIC_TASK_LOAD)
        )
        task = (await self._db.execute(statement)).scalar_one_or_none()
        if task is None:
            await self._db.rollback()
            raise TaskNotClaimableError
        await self._db.commit()
        claimed = _to_read(task)
        if not isinstance(claimed, RunningTaskRead):  # pragma: no cover — UPDATE fixes status
            msg = "Claim did not return a running task"
            raise TypeError(msg)
        return TaskClaim(task=claimed, lease_token=lease_token)

    @override
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        return await self._owned_lease_update(
            task_id,
            lease_token,
            values={
                "lease_expires_at": func.now(),
                "updated_at": func.now(),
            },
        )

    @override
    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None:
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.status == TaskStatus.RUNNING,
                Task.lease_token.is_not_distinct_from(lease_token),
            )
            .values(resource_id=resource_id, updated_at=datetime.now(UTC))
            .returning(Task.id)
        )
        updated = (await self._db.execute(statement)).scalar_one_or_none()
        if updated is None:
            await self._db.rollback()
            raise TaskLeaseLostError
        await self._db.commit()

    @override
    async def succeed(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        result: BaseModel,
    ) -> TaskRead:
        now = datetime.now(UTC)
        return await self._owned_lease_update(
            task_id,
            lease_token,
            values={
                "status": TaskStatus.SUCCEEDED,
                "result": _serialize(result),
                "payload": _terminal_payload(),
                "lease_token": None,
                "lease_expires_at": None,
                "finished_at": now,
                "updated_at": now,
            },
            header_result=result if isinstance(result, DocumentTransformResult) else None,
        )

    @override
    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead:
        if not error_code:
            msg = "error_code must not be empty"
            raise ValueError(msg)
        now = datetime.now(UTC)
        return await self._owned_lease_update(
            task_id,
            lease_token,
            values={
                "status": TaskStatus.FAILED,
                "error_code": error_code,
                "error_detail": _persisted_error_detail(error_detail),
                "payload": _terminal_payload(),
                "lease_token": None,
                "lease_expires_at": None,
                "finished_at": now,
                "updated_at": now,
            },
        )

    @override
    async def discard_pending(self, task_id: TaskId, *, owner_user_id: UserId) -> bool:
        statement = (
            delete(Task)
            .where(
                Task.id == task_id,
                Task.owner_user_id == owner_user_id,
                Task.status == TaskStatus.PENDING,
                Task.resource_id.is_(None),
            )
            .returning(Task.id)
        )
        deleted = (await self._db.execute(statement)).scalar_one_or_none()
        await self._db.commit()
        return deleted is not None

    @override
    async def cancel(self, task_id: TaskId, *, owner_user_id: UserId) -> TaskRead:
        now = datetime.now(UTC)
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.owner_user_id == owner_user_id,
                Task.status == TaskStatus.PENDING,
            )
            .values(
                status=TaskStatus.CANCELLED,
                payload=_terminal_payload(),
                finished_at=now,
                updated_at=now,
            )
            .returning(Task)
            .options(_PUBLIC_TASK_LOAD)
        )
        task = (await self._db.execute(statement)).scalar_one_or_none()
        if task is None:
            await self._db.rollback()
            raise TaskNotClaimableError
        await self._db.commit()
        return _to_read(task)

    @override
    async def get_for_owner(self, task_id: TaskId, owner_user_id: UserId) -> TaskRead:
        task = (
            await self._db.execute(
                select(Task)
                .options(_PUBLIC_TASK_LOAD)
                .where(
                    Task.id == task_id,
                    Task.owner_user_id == owner_user_id,
                    Task.expires_at > func.now(),
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise TaskNotFoundError
        return _to_read(task)

    @override
    async def get_for_owner_bounded_result(
        self,
        task_id: TaskId,
        owner_user_id: UserId,
        *,
        max_result_bytes: int,
        max_findings: int,
        max_model_entries: int,
        max_tool_entries: int,
    ) -> TaskRead:
        if min(max_result_bytes, max_findings, max_model_entries, max_tool_entries) < 1:
            raise ValueError
        findings = Task.result["findings"]
        model_ids = Task.result["model_ids"]
        valid_result = and_(
            func.jsonb_typeof(Task.result) == "object",
            func.octet_length(cast(Task.result, Text)).between(1, max_result_bytes),
            case(
                (func.jsonb_typeof(findings) == "array", func.jsonb_array_length(findings)),
                else_=-1,
            ).between(0, max_findings),
            case(
                (func.jsonb_typeof(model_ids) == "array", func.jsonb_array_length(model_ids)),
                else_=-1,
            ).between(0, max_model_entries),
        )
        task = (
            await self._db.execute(
                select(Task)
                .options(_PUBLIC_TASK_LOAD)
                .where(
                    Task.id == task_id,
                    Task.owner_user_id == owner_user_id,
                    Task.expires_at > func.now(),
                    # Filter before asyncpg materializes a terminal JSON result.
                    or_(Task.status != TaskStatus.SUCCEEDED, valid_result),
                )
                .with_for_update(read=True)
            )
        ).scalar_one_or_none()
        if task is None:
            raise TaskNotFoundError
        return _to_read(task)

    @override
    async def get_qa_permission_evidence(
        self,
        task_id: TaskId,
        owner_user_id: UserId,
        *,
        max_evidence_bytes: int,
    ) -> QaCopyPermissionEvidence:
        if max_evidence_bytes < 1:
            raise ValueError
        evidence = Task.result["qa_permission_evidence"]
        evidence_bytes = func.octet_length(cast(evidence, Text))
        row = (
            await self._db.execute(
                select(evidence.label("evidence"))
                .where(
                    Task.id == task_id,
                    Task.owner_user_id == owner_user_id,
                    Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                    Task.status == TaskStatus.SUCCEEDED,
                    Task.expires_at > func.now(),
                    func.jsonb_typeof(evidence) == "object",
                    evidence_bytes.between(1, max_evidence_bytes),
                )
                .with_for_update(read=True)
            )
        ).one_or_none()
        if row is None:
            raise TaskNotFoundError
        return QaCopyPermissionEvidence.model_validate(row.evidence)

    @override
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        task = (
            await self._db.execute(
                select(Task)
                .options(
                    load_only(
                        *_PUBLIC_TASK_COLUMNS, Task.owner_user_id, Task.payload, raiseload=True
                    )
                )
                .where(Task.id == task_id, Task.expires_at > func.now())
            )
        ).scalar_one_or_none()
        if task is None or task.kind != TaskKind.FILE_SMART_CREATE:
            raise TaskNotFoundError
        return SmartCreateTaskRecord.model_validate(
            {
                "owner_user_id": task.owner_user_id,
                "task": _task_read_payload(task),
                "payload": task.payload,
            }
        )

    @override
    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int:
        values: dict[str, object] = {
            "checkpoint": _serialize_checkpoint(checkpoint),
            "checkpoint_generation": Task.checkpoint_generation + 1,
            "updated_at": datetime.now(UTC),
        }
        if isinstance(checkpoint, InPlaceCheckpoint):
            values["resource_id"] = checkpoint.document_id
        elif checkpoint.output is not None:
            values["resource_id"] = checkpoint.output.file_id
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                Task.status == TaskStatus.RUNNING,
                Task.lease_token.is_not_distinct_from(lease_token),
                Task.checkpoint_generation == expected_generation,
            )
            .values(**values)
            .returning(Task.checkpoint_generation)
        )
        generation = (await self._db.execute(statement)).scalar_one_or_none()
        if generation is None:
            await self._db.rollback()
            raise TaskLeaseLostError
        await self._db.commit()
        return int(generation)

    @override
    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord:
        task = (
            await self._db.execute(
                select(Task)
                .options(
                    load_only(
                        *_PUBLIC_TASK_COLUMNS,
                        Task.owner_user_id,
                        Task.payload,
                        Task.checkpoint,
                        Task.checkpoint_generation,
                        raiseload=True,
                    )
                )
                .where(Task.id == task_id, Task.expires_at > func.now())
            )
        ).scalar_one_or_none()
        if task is None or task.kind != TaskKind.FILE_DOCUMENT_TRANSFORM:
            raise TaskNotFoundError
        terminal = task.status in {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
        payload = (
            None
            if terminal and task.payload == _TERMINAL_REDACTED_PAYLOAD
            else _deserialize_document_transform_payload(task.payload)
        )
        return DocumentTransformTaskRecord.model_validate(
            {
                "owner_user_id": task.owner_user_id,
                "task": _task_read_payload(task),
                "payload": payload,
                "checkpoint": (
                    _deserialize_checkpoint(task.checkpoint)
                    if task.checkpoint is not None
                    else None
                ),
                "checkpoint_generation": task.checkpoint_generation,
            }
        )

    async def _maybe_existing(
        self,
        owner_user_id: uuid.UUID,
        kind: TaskKind,
        idempotency_key: IdempotencyKey,
    ) -> Task | None:
        return (
            await self._db.execute(
                select(Task)
                .options(_REPLAY_TASK_LOAD)
                .where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == kind,
                    Task.id.in_(_task_ids_for_key(UserId(owner_user_id), kind, idempotency_key)),
                    Task.expires_at > func.now(),
                )
            )
        ).scalar_one_or_none()

    async def _bind_replay_key(
        self,
        *,
        owner_user_id: uuid.UUID,
        kind: TaskKind,
        idempotency_key: IdempotencyKey,
        task_id: TaskId,
    ) -> None:
        await self._db.execute(
            pg_insert(TaskReplayKey)
            .values(
                owner_user_id=owner_user_id,
                kind=kind,
                idempotency_key=idempotency_key,
                task_id=task_id,
            )
            .on_conflict_do_update(
                index_elements=["owner_user_id", "kind", "idempotency_key"],
                set_={"task_id": task_id},
            )
        )

    async def _maybe_retained_terminal_replay(
        self,
        *,
        owner_user_id: uuid.UUID,
        kind: TaskKind,
        idempotency_key: IdempotencyKey,
        request_hash: str,
        admit: Callable[[TaskRead], Awaitable[bool]] | None,
    ) -> TaskReservation | None:
        terminal = (
            await self._db.execute(
                select(Task)
                .options(_PUBLIC_TASK_LOAD)
                .where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == kind,
                    Task.status == TaskStatus.SUCCEEDED,
                    Task.request_hash == request_hash,
                    Task.finished_at.is_not(None),
                    Task.expires_at > func.now(),
                    Task.result["pipeline_version"].astext.in_(CURRENT_PIPELINE_VERSIONS),
                    Task.result["brand_profile_sha256"].astext == CURRENT_BRAND_MANIFEST.sha256,
                )
                .order_by(Task.finished_at.desc(), Task.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if terminal is None:
            return None
        try:
            terminal_read = _to_read(terminal)
        except BaseException:
            await self._db.rollback()
            raise
        if admit is None:
            await self._db.rollback()
            message = "Terminal document-transform replay requires an admission callback"
            raise RuntimeError(message)
        try:
            admitted = await admit(terminal_read)
        except BaseException:
            await self._db.rollback()
            raise
        if not admitted:
            return None
        # The provider check above is an external await. Re-read and lock the
        # exact terminal row before publishing an alias so expiry, cancellation,
        # or replacement during that await cannot create an immediately stale
        # replay binding.
        terminal = (
            await self._db.execute(
                select(Task)
                .options(_PUBLIC_TASK_LOAD)
                .where(
                    Task.id == terminal.id,
                    Task.owner_user_id == owner_user_id,
                    Task.kind == kind,
                    Task.status == TaskStatus.SUCCEEDED,
                    Task.request_hash == request_hash,
                    Task.finished_at.is_not(None),
                    Task.expires_at > func.clock_timestamp(),
                    Task.result["pipeline_version"].astext.in_(CURRENT_PIPELINE_VERSIONS),
                    Task.result["brand_profile_sha256"].astext == CURRENT_BRAND_MANIFEST.sha256,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if terminal is None:
            return None
        terminal_read = _to_read(terminal)
        await self._bind_replay_key(
            owner_user_id=owner_user_id,
            kind=kind,
            idempotency_key=idempotency_key,
            task_id=terminal.id,
        )
        await self._db.commit()
        return TaskReservation(task=terminal_read, created=False)

    @override
    async def get_document_transform_admission(
        self, owner_user_id: uuid.UUID, idempotency_key: IdempotencyKey
    ) -> DocumentTransformAdmission:
        # Project only public admission identity in one retained-row read. Never
        # load/decrypt payloads or checkpoints, nor renew retention or dispatch.
        row = (
            await self._db.execute(
                select(Task.id, Task.status).where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                    Task.expires_at > func.now(),
                    Task.id.in_(
                        _task_ids_for_key(
                            UserId(owner_user_id), TaskKind.FILE_DOCUMENT_TRANSFORM, idempotency_key
                        )
                    ),
                )
            )
        ).one_or_none()
        if row is None:
            raise TaskNotFoundError
        return DocumentTransformAdmission(task_id=row.id, status=row.status)

    async def _owned_lease_update(
        self,
        task_id: TaskId,
        lease_token: uuid.UUID,
        *,
        values: dict[str, object],
        header_result: DocumentTransformResult | None = None,
    ) -> TaskRead:
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.status == TaskStatus.RUNNING,
                Task.lease_token.is_not_distinct_from(lease_token),
            )
            .values(**values)
            .returning(Task)
        )
        if header_result is not None and header_result.header_identity is not None:
            statement = statement.options(
                load_only(
                    *_PUBLIC_TASK_COLUMNS, Task.owner_user_id, Task.checkpoint, raiseload=True
                )
            )
        else:
            statement = statement.options(_PUBLIC_TASK_LOAD)
        task = (await self._db.execute(statement)).scalar_one_or_none()
        if task is None:
            await self._db.rollback()
            raise TaskLeaseLostError
        try:
            if header_result is not None and header_result.header_identity is not None:
                await self._persist_header_identity(task, header_result)
        except Exception:
            await self._db.rollback()
            raise
        await self._db.commit()
        return _to_read(task)

    async def _persist_header_identity(self, task: Task, result: DocumentTransformResult) -> None:
        try:
            checkpoint = (
                _deserialize_checkpoint(task.checkpoint) if task.checkpoint is not None else None
            )
        except (InvalidToken, TypeError, ValueError) as exc:
            raise OutputConflictError from exc
        evidence = result.header_identity
        if (
            not isinstance(checkpoint, CopyCheckpoint)
            or checkpoint.stage not in {CopyCheckpointStage.VERIFIED, CopyCheckpointStage.ATTESTED}
            or checkpoint.result is None
            or evidence is None
            or checkpoint.header_finalization is None
        ):
            raise OutputConflictError
        finalized = checkpoint.result
        _validate_header_attestation(checkpoint, finalized, result)
        if (
            finalized.header_identity != evidence
            or evidence.operation_id != task.id
            or evidence.file_id != result.output.file_id
            or not finalized.validation.protected_content_preserved
            or not finalized.validation.structure_preserved
        ):
            raise OutputConflictError
        if not _terminal_result_matches(finalized, result):
            raise OutputConflictError
        if finalized.output_revision_id is None or finalized.output.file_id != evidence.file_id:
            raise OutputConflictError
        # Alias receipts are added after provider finalization. Hash the exact
        # pre-alias checkpoint result whose Drive completion was read back.
        result_digest = hashlib.sha256(
            json.dumps(
                _public_document_transform_payload(finalized),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        revision_digest = hashlib.sha256(finalized.output_revision_id.encode()).hexdigest()
        if (
            checkpoint.header_finalization.result_sha256 != result_digest
            or checkpoint.header_finalization.verified_revision_sha256 != revision_digest
            or checkpoint.managed_headers
            != {str(header.tab_id): str(header.header_id) for header in evidence.headers}
        ):
            raise OutputConflictError
        value = evidence.model_dump(mode="json")
        await self._db.execute(
            pg_insert(DocumentHeaderProvenance)
            .values(
                owner_user_id=task.owner_user_id,
                file_id=evidence.file_id,
                producer_operation_id=evidence.operation_id,
                evidence=value,
                result_sha256=result_digest,
                verified_revision_sha256=revision_digest,
            )
            .on_conflict_do_nothing()
        )
        stored = (
            await self._db.execute(
                select(DocumentHeaderProvenance).where(
                    DocumentHeaderProvenance.owner_user_id == task.owner_user_id,
                    DocumentHeaderProvenance.file_id == evidence.file_id,
                )
            )
        ).scalar_one()
        if (
            stored.producer_operation_id != evidence.operation_id
            or stored.evidence != value
            or stored.result_sha256 != result_digest
            or stored.verified_revision_sha256 != revision_digest
        ):
            raise OutputConflictError


def _validated_active_count(value: object) -> int:
    if not isinstance(value, int):
        msg = "Active task count query returned an invalid value"
        raise TypeError(msg)
    return value


def _task_ids_for_key(
    owner_user_id: UserId, kind: TaskKind, idempotency_key: IdempotencyKey
) -> CompoundSelect[TaskId]:
    # Both keys have an owner/kind/key index. Resolve their at-most-two IDs
    # before reading Task rows instead of scanning the owner's retained history.
    return union_all(
        select(Task.id).where(
            Task.owner_user_id == owner_user_id,
            Task.kind == kind,
            Task.idempotency_key == idempotency_key,
        ),
        select(TaskReplayKey.task_id).where(
            TaskReplayKey.owner_user_id == owner_user_id,
            TaskReplayKey.kind == kind,
            TaskReplayKey.idempotency_key == idempotency_key,
        ),
    )


def _validate_header_attestation(
    checkpoint: CopyCheckpoint,
    finalized: DocumentTransformResult | QaDocumentTransformResult,
    result: DocumentTransformResult,
) -> None:
    if checkpoint.stage is CopyCheckpointStage.ATTESTED:
        if (
            checkpoint.qa_destination_permission is None
            or checkpoint.qa_permission_evidence is None
            or not isinstance(finalized, QaDocumentTransformResult)
        ):
            raise OutputConflictError
        if finalized.qa_permission_evidence != checkpoint.qa_permission_evidence or not isinstance(
            result, QaDocumentTransformResult
        ):
            raise OutputConflictError
        if result.qa_permission_evidence != checkpoint.qa_permission_evidence:
            raise OutputConflictError
    elif checkpoint.qa_permission_evidence is not None or isinstance(
        finalized, QaDocumentTransformResult
    ):
        # A QA task cannot persist provenance from the provisional VERIFIED
        # checkpoint that precedes its terminal owner-only attestation.
        raise OutputConflictError


def _serialize(model: BaseModel) -> dict[str, JsonValue]:
    serialized = model.model_dump(mode="json")
    if isinstance(model, DocumentTransformRequest):
        # Preserve pre-destination idempotency hashes for every legacy request shape.
        if model.copy_destination_folder_id is None:
            serialized.pop("copy_destination_folder_id", None)
        # Preserve the pre-output_strategy payload and idempotency hash for legacy copy calls.
        if model.output_strategy is OutputStrategy.COPY:
            serialized.pop("output_strategy", None)
    return _TaskJsonObject.model_validate(serialized).root


def _public_document_transform_payload(
    result: DocumentTransformResult,
) -> dict[str, JsonValue]:
    serialized = result.model_dump(mode="json")
    serialized.pop("qa_permission_evidence", None)
    return _TaskJsonObject.model_validate(serialized).root


def _terminal_result_matches(
    checkpoint_result: DocumentTransformResult,
    submitted_result: DocumentTransformResult,
) -> bool:
    checkpoint_payload = _TaskJsonObject.model_validate(
        checkpoint_result.model_dump(mode="json")
    ).root
    submitted_payload = _TaskJsonObject.model_validate(
        submitted_result.model_dump(mode="json")
    ).root
    checkpoint_link = (
        checkpoint_result.short_link_alias,
        checkpoint_result.short_link_status,
    )
    submitted_link = (
        submitted_result.short_link_alias,
        submitted_result.short_link_status,
    )
    allowed_enrichment = checkpoint_link == (None, None) and (
        (submitted_link[0] is not None and submitted_link[1] == "ready")
        or submitted_link == (None, "unavailable")
    )
    if submitted_link != checkpoint_link and not allowed_enrichment:
        return False
    submitted_payload["short_link_alias"] = checkpoint_payload["short_link_alias"]
    submitted_payload["short_link_status"] = checkpoint_payload["short_link_status"]
    return submitted_payload == checkpoint_payload


def _payload_for_storage(
    *,
    kind: TaskKind,
    payload: BaseModel,
    serialized: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if (
        kind is not TaskKind.FILE_DOCUMENT_TRANSFORM
        or not isinstance(payload, DocumentTransformRequest)
        or not isinstance(payload.source, MarkdownSource)
        or payload.source.kind != "markdown"
    ):
        return serialized
    source = serialized.get("source")
    if not isinstance(source, dict):  # pragma: no cover - validated model invariant
        msg = "Markdown task payload is missing its source object"
        raise TypeError(msg)
    content = source.get("content")
    if not isinstance(content, str):  # pragma: no cover - validated model invariant
        msg = "Markdown task payload is missing its content"
        raise TypeError(msg)
    protected_source = {key: value for key, value in source.items() if key != "content"}
    protected_source.update(
        {
            _MARKDOWN_CIPHERTEXT_FIELD: encrypt_cache(content),
            _MARKDOWN_SHA256_FIELD: hashlib.sha256(content.encode()).hexdigest(),
            _MARKDOWN_ENCRYPTION_FIELD: _MARKDOWN_ENCRYPTION_VERSION,
        }
    )
    protected = dict(serialized)
    protected["source"] = protected_source
    return _TaskJsonObject.model_validate(protected).root


def _deserialize_document_transform_payload(
    raw: Mapping[str, object],
) -> DocumentTransformRequest | QaDocumentTransformRequest:
    source = raw.get("source")
    if not isinstance(source, dict) or source.get(_MARKDOWN_ENCRYPTION_FIELD) is None:
        payload_type = (
            QaDocumentTransformRequest
            if raw.get("qa_contract") == "owner_only_v1"
            else DocumentTransformRequest
        )
        return payload_type.model_validate(raw)
    if source.get(_MARKDOWN_ENCRYPTION_FIELD) != _MARKDOWN_ENCRYPTION_VERSION:
        msg = "Markdown task payload uses an unsupported encryption version"
        raise ValueError(msg)
    ciphertext = source.get(_MARKDOWN_CIPHERTEXT_FIELD)
    expected_sha256 = source.get(_MARKDOWN_SHA256_FIELD)
    if (
        "content" in source
        or not isinstance(ciphertext, str)
        or not isinstance(expected_sha256, str)
    ):
        msg = "Encrypted Markdown task payload is invalid"
        raise ValueError(msg)
    content = decrypt_cache(ciphertext)
    if not hmac.compare_digest(hashlib.sha256(content.encode()).hexdigest(), expected_sha256):
        msg = "Encrypted Markdown task payload hash does not match"
        raise ValueError(msg)
    encrypted_fields = {
        _MARKDOWN_CIPHERTEXT_FIELD,
        _MARKDOWN_SHA256_FIELD,
        _MARKDOWN_ENCRYPTION_FIELD,
    }
    plaintext_source = {key: value for key, value in source.items() if key not in encrypted_fields}
    plaintext_source["content"] = content
    plaintext = dict(raw)
    plaintext["source"] = plaintext_source
    return DocumentTransformRequest.model_validate(plaintext)


def _terminal_payload() -> object:
    return case(
        (
            Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
            literal(_TERMINAL_REDACTED_PAYLOAD, type_=JSONB),
        ),
        else_=Task.payload,
    )


def _serialize_checkpoint(checkpoint: DocumentTransformCheckpoint) -> dict[str, JsonValue]:
    serialized = checkpoint.model_dump(mode="json")
    if isinstance(checkpoint, CopyCheckpoint | WordCopyCheckpoint):
        plaintext = json.dumps(serialized, ensure_ascii=False, separators=(",", ":"))
        if len(plaintext.encode()) > _MAX_CHECKPOINT_PLAN_BYTES:
            raise CheckpointTooLargeError
        return {
            "kind": checkpoint.kind,
            "version": 1,
            _COPY_CHECKPOINT_CIPHERTEXT_FIELD: encrypt_cache(plaintext),
        }
    plan = {field: serialized.pop(field) for field in _CHECKPOINT_PLAN_FIELDS}
    plaintext = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    if len(plaintext.encode()) > _MAX_CHECKPOINT_PLAN_BYTES:
        raise CheckpointTooLargeError
    serialized["plan_ciphertext"] = encrypt_cache(plaintext)
    return _TaskJsonObject.model_validate(serialized).root


def _deserialize_checkpoint(raw: Mapping[str, object]) -> DocumentTransformCheckpoint:
    if raw.get("kind") in {"copy", "word_copy"}:
        ciphertext = raw.get(_COPY_CHECKPOINT_CIPHERTEXT_FIELD)
        if not isinstance(ciphertext, str):
            msg = "Copy transform checkpoint is missing its encrypted payload"
            raise TypeError(msg)
        payload = json.loads(decrypt_cache(ciphertext))
        if not isinstance(payload, dict):
            msg = "Copy transform checkpoint payload is invalid"
            raise ValueError(msg)
        checkpoint_type = WordCopyCheckpoint if raw.get("kind") == "word_copy" else CopyCheckpoint
        return checkpoint_type.model_validate(payload)
    metadata = dict(raw)
    ciphertext = metadata.pop("plan_ciphertext", None)
    if not isinstance(ciphertext, str):
        msg = "Document transform checkpoint is missing encrypted plan"
        raise TypeError(msg)
    plan = json.loads(decrypt_cache(ciphertext))
    if not isinstance(plan, dict) or set(plan) != _CHECKPOINT_PLAN_FIELDS:
        msg = "Document transform checkpoint plan is invalid"
        raise ValueError(msg)
    metadata.update(plan)
    return InPlaceCheckpoint.model_validate(metadata)


def _persisted_error_detail(error_detail: str | None) -> str | None:
    if error_detail is None:
        return None
    return _TASK_FAILURE_DETAIL[:_MAX_ERROR_DETAIL_LENGTH]


def _request_hash(payload: dict[str, JsonValue]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _task_read_payload(task: Task) -> dict[str, object]:
    result = task.result
    if (
        task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM
        and isinstance(result, dict)
        and "qa_permission_evidence" in result
    ):
        result = dict(result)
        result.pop("qa_permission_evidence", None)
    return {
        "id": task.id,
        "kind": task.kind,
        "status": task.status,
        "result": result,
        "resource_id": task.resource_id,
        "error_code": task.error_code,
        "error_detail": task.error_detail,
        "attempt_count": task.attempt_count,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "created_at": task.created_at,
    }


def _to_read(task: Task) -> TaskRead:
    return _TaskReadPayload.model_validate(_task_read_payload(task)).root
