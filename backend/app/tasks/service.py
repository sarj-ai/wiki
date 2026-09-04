from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
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
from app.document_transform.errors import CheckpointTooLargeError
from app.document_transform.schemas import (
    CopyCheckpoint,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DriveFileSource,
    InPlaceCheckpoint,
    MarkdownSource,
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
        checkpoint: DocumentTransformCheckpoint,
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
        persisted_payload = _payload_for_storage(kind=kind, payload=payload, serialized=serialized)
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
                payload=persisted_payload,
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
                    Task.idempotency_key == idempotency_key,
                    Task.expires_at > func.now(),
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
    if isinstance(checkpoint, CopyCheckpoint):
        plaintext = json.dumps(
            serialized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if len(plaintext.encode()) > _MAX_CHECKPOINT_PLAN_BYTES:
            raise CheckpointTooLargeError
        return {
            "kind": "copy",
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
    if raw.get("kind") == "copy":
        ciphertext = raw.get(_COPY_CHECKPOINT_CIPHERTEXT_FIELD)
        if not isinstance(ciphertext, str):
            msg = "Copy transform checkpoint is missing its encrypted payload"
            raise TypeError(msg)
        payload = json.loads(decrypt_cache(ciphertext))
        if not isinstance(payload, dict):
            msg = "Copy transform checkpoint payload is invalid"
            raise ValueError(msg)
        return CopyCheckpoint.model_validate(payload)
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
