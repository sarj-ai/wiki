from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
from typing import override
import uuid

from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import and_, case, delete, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

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
    WordCopyCheckpoint,
)
from app.document_transform.versions import CURRENT_PIPELINE_VERSIONS
from app.identifiers import FileId, IdempotencyKey, TaskId
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
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
    TaskStatus,
)


_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_TASK_READ: TypeAdapter[TaskRead] = TypeAdapter(TaskRead)
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


class TaskService(ABC):
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
    async def discard_pending(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead: ...

    @abstractmethod
    async def get_for_owner(self, task_id: TaskId, owner_user_id: uuid.UUID) -> TaskRead: ...

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
    async def pending_document_transform_dispatches(self, *, limit: int = 50) -> list[TaskId]:
        if not 1 <= limit <= _MAX_DISPATCH_RECONCILIATION_BATCH:
            msg = "Dispatch reconciliation limit must be between 1 and 100"
            raise ValueError(msg)
        statement = (
            select(Task.id)
            .where(
                Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                Task.status == TaskStatus.PENDING,
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
            await self._db.execute(
                select(func.pg_advisory_xact_lock(func.hashtextextended(str(owner_user_id), 0)))
            )
            existing = await self._maybe_existing(owner_user_id, kind, idempotency_key)
            if existing is not None:
                if existing.request_hash != request_hash:
                    await self._db.rollback()
                    raise TaskIdempotencyConflictError
                await self._db.commit()
                return TaskReservation(task=_to_read(existing), created=False)
            active_row = (
                await self._db.execute(
                    select(Task, (Task.expires_at <= func.now()).label("expired"))
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
                        (
                            and_(
                                Task.payload["source"]["kind"].astext == "drive_file",
                                Task.payload["source"]["file_id"].astext
                                == document_transform_payload.source.file_id,
                            )
                            if isinstance(document_transform_payload.source, DriveFileSource)
                            else and_(
                                Task.payload["source"]["kind"].astext == "markdown",
                                Task.request_hash == request_hash,
                            )
                        ),
                    )
                    .order_by(Task.created_at, Task.id)
                    .limit(1)
                )
            ).one_or_none()
            if active_row is not None:
                active = active_row[0]
                # A new key is a fresh access request, unlike an exact-key
                # replay. Recheck scope before disclosing an active task.
                try:
                    if admit is not None:
                        await admit()
                except BaseException:
                    await self._db.rollback()
                    raise
                if active_row[1] or active.request_hash != request_hash:
                    await self._db.rollback()
                    raise TaskTargetBusyError
                await self._bind_replay_key(
                    owner_user_id=owner_user_id,
                    kind=kind,
                    idempotency_key=idempotency_key,
                    task_id=active.id,
                )
                await self._db.commit()
                return TaskReservation(task=_to_read(active), created=False)
            # A fresh key is a fresh access request. Reauthorize before looking
            # up a completed task so prior output existence is never disclosed
            # after source or destination access has been revoked.
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
            active_count = (
                await self._db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.owner_user_id == owner_user_id,
                        Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                        Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)),
                        Task.expires_at > func.now(),
                    )
                )
            ).scalar_one_or_none()
            if not isinstance(active_count, int):
                msg = "Active document-transform task count query returned an invalid value"
                raise TypeError(msg)
            if active_count >= _MAX_ACTIVE_DOCUMENT_TRANSFORMS_PER_OWNER:
                await self._db.rollback()
                raise TaskAdmissionLimitError
            if in_place_source is not None:
                active_in_place_count = (
                    await self._db.execute(
                        select(func.count())
                        .select_from(Task)
                        .where(
                            Task.owner_user_id == owner_user_id,
                            Task.in_place_target.is_not(None),
                            Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)),
                            Task.expires_at > func.now(),
                        )
                    )
                ).scalar_one_or_none()
                if not isinstance(active_in_place_count, int):
                    msg = "Active in-place task count query returned an invalid value"
                    raise TypeError(msg)
                if active_in_place_count >= _MAX_ACTIVE_IN_PLACE_TASKS_PER_OWNER:
                    await self._db.rollback()
                    raise TaskAdmissionLimitError
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
        task = await self._owned_lease_update(
            task_id,
            lease_token,
            values={"resource_id": resource_id, "updated_at": datetime.now(UTC)},
        )
        del task

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
    async def discard_pending(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> bool:
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
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead:
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
        )
        task = (await self._db.execute(statement)).scalar_one_or_none()
        if task is None:
            await self._db.rollback()
            raise TaskNotClaimableError
        await self._db.commit()
        return _to_read(task)

    @override
    async def get_for_owner(self, task_id: TaskId, owner_user_id: uuid.UUID) -> TaskRead:
        task = (
            await self._db.execute(
                select(Task).where(
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
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        task = (
            await self._db.execute(
                select(Task).where(Task.id == task_id, Task.expires_at > func.now())
            )
        ).scalar_one_or_none()
        if task is None or task.kind != TaskKind.FILE_SMART_CREATE:
            raise TaskNotFoundError
        return SmartCreateTaskRecord.model_validate(
            {
                "owner_user_id": task.owner_user_id,
                "task": _to_read(task),
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
                select(Task).where(Task.id == task_id, Task.expires_at > func.now())
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
                "task": _to_read(task),
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
                select(Task).where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == kind,
                    or_(
                        Task.idempotency_key == idempotency_key,
                        Task.id.in_(
                            select(TaskReplayKey.task_id).where(
                                TaskReplayKey.owner_user_id == owner_user_id,
                                TaskReplayKey.kind == kind,
                                TaskReplayKey.idempotency_key == idempotency_key,
                            )
                        ),
                    ),
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
                    or_(
                        Task.idempotency_key == idempotency_key,
                        Task.id.in_(
                            select(TaskReplayKey.task_id).where(
                                TaskReplayKey.owner_user_id == owner_user_id,
                                TaskReplayKey.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                                TaskReplayKey.idempotency_key == idempotency_key,
                            )
                        ),
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
        checkpoint = (
            _deserialize_checkpoint(task.checkpoint) if task.checkpoint is not None else None
        )
        evidence = result.header_identity
        if (
            not isinstance(checkpoint, CopyCheckpoint)
            or checkpoint.stage is not CopyCheckpointStage.VERIFIED
            or checkpoint.result is None
            or evidence is None
            or checkpoint.header_finalization is None
        ):
            raise OutputConflictError
        finalized = checkpoint.result
        if (
            finalized.header_identity != evidence
            or evidence.operation_id != task.id
            or evidence.file_id != result.output.file_id
            or not finalized.validation.protected_content_preserved
            or not finalized.validation.structure_preserved
        ):
            raise OutputConflictError
        if finalized.output_revision_id is None or finalized.output.file_id != evidence.file_id:
            raise OutputConflictError
        # Alias receipts are added after provider finalization. Hash the exact
        # pre-alias checkpoint result whose Drive completion was read back.
        result_digest = hashlib.sha256(
            json.dumps(
                finalized.model_dump(mode="json"),
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


def _serialize(model: BaseModel) -> dict[str, JsonValue]:
    serialized = model.model_dump(mode="json")
    if isinstance(model, DocumentTransformRequest):
        # Preserve pre-destination idempotency hashes for every legacy request shape.
        if model.copy_destination_folder_id is None:
            serialized.pop("copy_destination_folder_id", None)
        # Preserve the pre-output_strategy payload and idempotency hash for legacy copy calls.
        if model.output_strategy is OutputStrategy.COPY:
            serialized.pop("output_strategy", None)
    return _JSON_OBJECT.validate_python(serialized)


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
    protected_source = dict(source)
    del protected_source["content"]
    protected_source.update(
        {
            _MARKDOWN_CIPHERTEXT_FIELD: encrypt_cache(content),
            _MARKDOWN_SHA256_FIELD: hashlib.sha256(content.encode()).hexdigest(),
            _MARKDOWN_ENCRYPTION_FIELD: _MARKDOWN_ENCRYPTION_VERSION,
        }
    )
    protected = dict(serialized)
    protected["source"] = protected_source
    return _JSON_OBJECT.validate_python(protected)


def _deserialize_document_transform_payload(
    raw: Mapping[str, object],
) -> DocumentTransformRequest:
    source = raw.get("source")
    if not isinstance(source, dict) or source.get(_MARKDOWN_ENCRYPTION_FIELD) is None:
        return DocumentTransformRequest.model_validate(raw)
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
    plaintext_source = dict(source)
    for field in (
        _MARKDOWN_CIPHERTEXT_FIELD,
        _MARKDOWN_SHA256_FIELD,
        _MARKDOWN_ENCRYPTION_FIELD,
    ):
        del plaintext_source[field]
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
        plaintext = json.dumps(
            serialized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if len(plaintext.encode()) > _MAX_CHECKPOINT_PLAN_BYTES:
            raise CheckpointTooLargeError
        return {
            "kind": checkpoint.kind,
            "version": 1,
            _COPY_CHECKPOINT_CIPHERTEXT_FIELD: encrypt_cache(plaintext),
        }
    plan = {field: serialized.pop(field) for field in _CHECKPOINT_PLAN_FIELDS}
    plaintext = json.dumps(plan, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(plaintext.encode()) > _MAX_CHECKPOINT_PLAN_BYTES:
        raise CheckpointTooLargeError
    serialized["plan_ciphertext"] = encrypt_cache(plaintext)
    return _JSON_OBJECT.validate_python(serialized)


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


def _to_read(task: Task) -> TaskRead:
    return _TASK_READ.validate_python(task, from_attributes=True)
