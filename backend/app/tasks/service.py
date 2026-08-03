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

from app.identifiers import FileId, IdempotencyKey, TaskId
from app.models.task import Task
from app.tasks.errors import (
    TaskIdempotencyConflictError,
    TaskLeaseLostError,
    TaskNotClaimableError,
    TaskNotFoundError,
)
from app.tasks.schemas import (
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
        now = datetime.now(UTC)
        statement = (
            pg_insert(Task)
            .values(
                owner_user_id=owner_user_id,
                kind=kind,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                payload=serialized,
                expires_at=now + _DEFAULT_RETENTION,
            )
            .on_conflict_do_nothing(
                index_elements=[Task.owner_user_id, Task.kind, Task.idempotency_key]
            )
            .returning(Task)
        )
        task = (await self._db.execute(statement)).scalar_one_or_none()
        if task is not None:
            await self._db.commit()
            return TaskReservation(task=_to_read(task), created=True)

        existing = await self._existing(owner_user_id, kind, idempotency_key)
        if existing.request_hash != request_hash:
            raise TaskIdempotencyConflictError
        return TaskReservation(task=_to_read(existing), created=False)

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        if lease_duration <= timedelta(0):
            msg = "lease_duration must be positive"
            raise ValueError(msg)
        now = datetime.now(UTC)
        lease_token = uuid.uuid4()
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
                "error_detail": error_detail,
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
    return _JSON_OBJECT.validate_python(model.model_dump(mode="json"))


def _request_hash(payload: dict[str, JsonValue]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _to_read(task: Task) -> TaskRead:
    return _TASK_READ.validate_python(task, from_attributes=True)
