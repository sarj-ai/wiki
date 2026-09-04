from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
import hashlib
import json
from typing import override
import uuid

from pydantic import BaseModel, JsonValue, TypeAdapter
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption import decrypt_cache, encrypt_cache
from app.document_transform.errors import CheckpointTooLargeError
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    InPlaceCheckpoint,
    OutputStrategy,
)
from app.identifiers import FileId, IdempotencyKey, TaskId
from app.models.task import Task
from app.tasks.errors import (
    TaskAdmissionLimitError,
    TaskIdempotencyConflictError,
    TaskLeaseLostError,
    TaskNotClaimableError,
    TaskNotFoundError,
    TaskTargetBusyError,
)
from app.tasks.schemas import (
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
_CHECKPOINT_PLAN_FIELDS = frozenset({"requests", "replacements", "findings", "headers"})
_MAX_ACTIVE_IN_PLACE_TASKS_PER_OWNER = 10


class TaskService(ABC):
    @abstractmethod
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
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
        checkpoint: InPlaceCheckpoint,
    ) -> int:
        raise NotImplementedError


class PostgresTaskService(TaskService):
    def __init__(self, *, db: AsyncSession) -> None:
        self._db = db

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
    ) -> TaskReservation:
        serialized = _serialize(payload)
        request_hash = _request_hash(serialized)
        in_place_source = (
            payload.source
            if isinstance(payload, DocumentTransformRequest)
            and payload.output_strategy is OutputStrategy.IN_PLACE
            and isinstance(payload.source, DriveFileSource)
            else None
        )
        if in_place_source is not None:
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
            active_count = (
                await self._db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.owner_user_id == owner_user_id,
                        Task.in_place_target.is_not(None),
                        Task.status.in_((TaskStatus.PENDING, TaskStatus.RUNNING)),
                    )
                )
            ).scalar_one_or_none()
            if not isinstance(active_count, int):
                msg = "Active in-place task count query returned an invalid value"
                raise TypeError(msg)
            if active_count >= _MAX_ACTIVE_IN_PLACE_TASKS_PER_OWNER:
                await self._db.rollback()
                raise TaskAdmissionLimitError
        now = datetime.now(UTC)
        statement = (
            pg_insert(Task)
            .values(
                owner_user_id=owner_user_id,
                kind=kind,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                payload=serialized,
                in_place_target=(in_place_source.file_id if in_place_source else None),
                expires_at=now + _DEFAULT_RETENTION,
            )
            .on_conflict_do_nothing()
            .returning(Task)
        )
        task = (await self._db.execute(statement)).scalar_one_or_none()
        if task is not None:
            await self._db.commit()
            return TaskReservation(task=_to_read(task), created=True)

        existing = await self._maybe_existing(owner_user_id, kind, idempotency_key)
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
                "lease_token": None,
                "lease_expires_at": None,
                "finished_at": now,
                "updated_at": now,
            },
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
                "lease_token": None,
                "lease_expires_at": None,
                "finished_at": now,
                "updated_at": now,
                "expires_at": now + timedelta(days=30),
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
            .values(status=TaskStatus.CANCELLED, finished_at=now, updated_at=now)
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
                select(Task).where(Task.id == task_id, Task.owner_user_id == owner_user_id)
            )
        ).scalar_one_or_none()
        if task is None:
            raise TaskNotFoundError
        return _to_read(task)

    @override
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        task = await self._db.get(Task, task_id)
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
        checkpoint: InPlaceCheckpoint,
    ) -> int:
        statement = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                Task.status == TaskStatus.RUNNING,
                Task.lease_token.is_not_distinct_from(lease_token),
                Task.checkpoint_generation == expected_generation,
            )
            .values(
                checkpoint=_serialize_checkpoint(checkpoint),
                checkpoint_generation=Task.checkpoint_generation + 1,
                resource_id=checkpoint.document_id,
                updated_at=datetime.now(UTC),
            )
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
        task = await self._db.get(Task, task_id)
        if task is None or task.kind != TaskKind.FILE_DOCUMENT_TRANSFORM:
            raise TaskNotFoundError
        return DocumentTransformTaskRecord.model_validate(
            {
                "owner_user_id": task.owner_user_id,
                "task": _to_read(task),
                "payload": task.payload,
                "checkpoint": (
                    _deserialize_checkpoint(task.checkpoint)
                    if task.checkpoint is not None
                    else None
                ),
                "checkpoint_generation": task.checkpoint_generation,
            }
        )

    async def _existing(
        self,
        owner_user_id: uuid.UUID,
        kind: TaskKind,
        idempotency_key: IdempotencyKey,
    ) -> Task:
        task = (
            await self._db.execute(
                select(Task).where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == kind,
                    Task.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if task is None:  # pragma: no cover — ON CONFLICT waits for the winning transaction
            raise TaskNotFoundError
        return task

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
                    Task.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()

    async def _owned_lease_update(
        self,
        task_id: TaskId,
        lease_token: uuid.UUID,
        *,
        values: dict[str, object],
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
        await self._db.commit()
        return _to_read(task)


def _serialize(model: BaseModel) -> dict[str, JsonValue]:
    serialized = model.model_dump(mode="json")
    # Preserve the pre-output_strategy payload and idempotency hash for legacy copy calls.
    if isinstance(model, DocumentTransformRequest) and model.output_strategy is OutputStrategy.COPY:
        serialized.pop("output_strategy", None)
    return _JSON_OBJECT.validate_python(serialized)


def _serialize_checkpoint(checkpoint: InPlaceCheckpoint) -> dict[str, JsonValue]:
    serialized = checkpoint.model_dump(mode="json")
    plan = {field: serialized.pop(field) for field in _CHECKPOINT_PLAN_FIELDS}
    plaintext = json.dumps(plan, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(plaintext.encode()) > _MAX_CHECKPOINT_PLAN_BYTES:
        raise CheckpointTooLargeError
    serialized["plan_ciphertext"] = encrypt_cache(plaintext)
    return _JSON_OBJECT.validate_python(serialized)


def _deserialize_checkpoint(raw: dict[str, object]) -> InPlaceCheckpoint:
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
