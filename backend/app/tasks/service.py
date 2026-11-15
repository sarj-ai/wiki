from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import hmac
import json
from typing import Annotated, Literal, TypedDict, assert_never, override
import uuid

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator
from sqlalchemy import Text, and_, case, cast, delete, func, literal, or_, select, union_all, update
from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import CompoundSelect

from app.auth.encryption import decrypt_cache, encrypt_cache
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import CheckpointTooLargeError, OutputConflictError
from app.document_transform.schemas import (
    CopyCheckpointPayload,
    CopyCheckpointStage,
    CopyPlan,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    InPlaceCheckpointPayload,
    MarkdownSource,
    ModelPlanningCheckpoint,
    OutputStrategy,
    PlannedCopyCheckpoint,
    PlannedPresentationCheckpoint,
    PlannedWordCheckpoint,
    PresentationCheckpointPayload,
    PresentationCopyPlan,
    TransformMode,
    WordCheckpointPayload,
    WordCopyPlan,
)
from app.document_transform.unslop import UNSLOP_CATALOG_SHA256
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
    ActiveDocumentTransformTaskRecord,
    ActiveSmartCreateTaskRecord,
    DocumentTransformSucceededTaskRead,
    DocumentTransformTaskRecord,
    ExistingTaskReservation,
    NeedsReviewTaskRead,
    NewTaskReservation,
    PendingTaskRead,
    ReviewDocumentTransformTaskRecord,
    RunningTaskRead,
    SettledSmartCreateTaskRecord,
    SmartCreateCheckpoint,
    SmartCreateDispatch,
    SmartCreateTaskRecord,
    TaskAdmission,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
    TaskResult,
    TaskStatus,
    TerminalDocumentTransformTaskRecord,
)


class _TransformSlotPhase(StrEnum):
    LIVE = "live"
    REVIEW = "review"
    RETAINED_LEASE = "retained_lease"
    ABANDONED = "abandoned"
    EXPIRED_PENDING = "expired_pending"


class _FailureCheckpointPhase(StrEnum):
    ABSENT = "absent"
    ENCRYPTED_COPY = "encrypted_copy"
    UNCERTAIN = "uncertain"


class _TaskJsonObject(RootModel[dict[str, JsonValue]]):
    """Validate serialized task payloads without changing their JSON shape."""


class _EncryptedCopyEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    kind: Literal["copy", "word_copy", "presentation_copy", "model_planning"]
    version: Literal[1]
    checkpoint_ciphertext: str


class _EncryptedInPlaceEnvelope(BaseModel):
    """Transport metadata is checked by the complete checkpoint model after decryption."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    kind: Literal["in_place"]
    version: Literal[2]
    plan_ciphertext: str
    metadata: dict[str, JsonValue]

    @model_validator(mode="before")
    @classmethod
    def decode_wire_metadata(cls, value: object) -> dict[str, JsonValue]:
        raw = _TaskJsonObject.model_validate(value).root
        return {
            "kind": raw.get("kind"),
            "version": raw.get("version", 2),
            "plan_ciphertext": raw.get("plan_ciphertext"),
            "metadata": {
                field: item
                for field, item in raw.items()
                if field not in {"kind", "version", "plan_ciphertext"}
            },
        }


class _StoredCheckpointEnvelope(
    RootModel[
        Annotated[_EncryptedCopyEnvelope | _EncryptedInPlaceEnvelope, Field(discriminator="kind")]
    ]
):
    pass


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
    Task.lease_expires_at,
)
_PUBLIC_TASK_LOAD = load_only(*_PUBLIC_TASK_COLUMNS, raiseload=True)
_REPLAY_TASK_LOAD = load_only(
    *_PUBLIC_TASK_COLUMNS, Task.request_hash, Task.idempotency_key, raiseload=True
)


class TaskService(ABC):
    async def get_current_in_place_result(
        self,
        owner_user_id: UserId,
        file_id: FileId,
        mode: TransformMode,
        revision_id: DocsRevisionId,
    ) -> DocumentTransformResult | None:
        raise NotImplementedError

    async def get_admission(
        self, owner_user_id: UserId, kind: TaskKind, idempotency_key: IdempotencyKey
    ) -> TaskAdmission:
        raise NotImplementedError

    async def pending_document_transform_dispatches(self, *, limit: int = 50) -> list[TaskId]:
        raise NotImplementedError

    @abstractmethod
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: UserId,
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
        result: TaskResult,
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

    @abstractmethod
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord: ...

    async def begin_smart_create(
        self, task_id: TaskId, *, lease_token: uuid.UUID
    ) -> SmartCreateDispatch:
        raise NotImplementedError

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

    async def require_review(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
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
        if not isinstance(task, DocumentTransformSucceededTaskRead):
            return None
        return task.result if task.result.output_revision_id == revision_id else None

    @override
    async def pending_document_transform_dispatches(self, *, limit: int = 50) -> list[TaskId]:
        if not 1 <= limit <= _MAX_DISPATCH_RECONCILIATION_BATCH:
            msg = "Dispatch reconciliation limit must be between 1 and 100"
            raise ValueError(msg)
        await self._retain_expired_executions(limit=limit)
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

    async def _retain_expired_executions(self, *, limit: int) -> None:
        expired = (
            select(Task.id)
            .where(
                Task.status == TaskStatus.RUNNING,
                Task.expires_at <= func.now(),
                Task.lease_expires_at <= func.now(),
            )
            .order_by(Task.expires_at, Task.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        await self._db.execute(
            update(Task)
            .where(Task.id.in_(expired))
            .values(**_review_values(func.now()))
            .execution_options(synchronize_session=False)
        )
        await self._db.commit()

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: UserId,
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
                Task.status.in_(
                    (
                        TaskStatus.PENDING,
                        TaskStatus.SUCCEEDED,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELLED,
                    )
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
            return NewTaskReservation.model_validate({"task": task}, from_attributes=True)

        existing = await self._maybe_existing(owner_user_id, kind, idempotency_key)
        if existing is None and document_transform_payload is not None:
            # PostgreSQL may test the original key's uniqueness before the
            # unreferenced expiry DELETE CTE runs. The owner lock is still held;
            # retry once against this transaction's completed cleanup, without
            # repeating admission or changing the insertion intent.
            task = (await self._db.execute(statement)).scalar_one_or_none()
            if task is not None:
                await self._db.commit()
                return NewTaskReservation.model_validate({"task": task}, from_attributes=True)
        if existing is None:
            await self._db.rollback()
            raise TaskTargetBusyError
        if existing.request_hash != request_hash:
            raise TaskIdempotencyConflictError
        return ExistingTaskReservation(task=_to_read(existing))

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
            return ExistingTaskReservation(task=_to_read(existing))
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
    ) -> Row[Task, str] | None:
        source = payload.source
        source_predicate: ColumnElement[bool] = Task.request_hash == request_hash
        match source:
            case DriveFileSource():
                source_predicate = or_(
                    source_predicate,
                    and_(
                        Task.payload["source"]["kind"].astext == "drive_file",
                        Task.payload["source"]["file_id"].astext == source.file_id,
                    ),
                )
            case MarkdownSource():
                pass
            case _:
                assert_never(source)
        return (
            await self._db.execute(
                select(Task, _transform_slot_phase(func.now()).label("slot_phase"))
                .options(_REPLAY_TASK_LOAD)
                .where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                    Task.status.in_(
                        (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.NEEDS_REVIEW)
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
        active_row: Row[Task, str],
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
                select(Task, _transform_slot_phase(func.clock_timestamp()).label("slot_phase"))
                .options(_REPLAY_TASK_LOAD)
                .where(
                    Task.id == active_row[0].id,
                    Task.status.in_(
                        (TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.NEEDS_REVIEW)
                    ),
                )
                .with_for_update()
            )
        ).one_or_none()
        if refreshed is None:
            return None
        phase = _TransformSlotPhase(refreshed[1])
        match phase:
            case _TransformSlotPhase.EXPIRED_PENDING:
                return None
            case _TransformSlotPhase.RETAINED_LEASE:
                await self._db.rollback()
                raise TaskTargetBusyError
            case _TransformSlotPhase.ABANDONED:
                retained = (
                    await self._db.execute(
                        update(Task)
                        .where(Task.id == refreshed[0].id)
                        .values(**_review_values(func.clock_timestamp()))
                        .returning(Task)
                        .options(_REPLAY_TASK_LOAD)
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one()
            case _TransformSlotPhase.LIVE | _TransformSlotPhase.REVIEW:
                retained = refreshed[0]
            case _:
                assert_never(phase)
        if retained.request_hash != request_hash:
            if phase is _TransformSlotPhase.ABANDONED:
                await self._db.commit()
            else:
                await self._db.rollback()
            raise TaskTargetBusyError
        if retained.idempotency_key != idempotency_key:
            await self._bind_replay_key(
                owner_user_id=owner_user_id,
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                idempotency_key=idempotency_key,
                task_id=retained.id,
            )
        await self._db.commit()
        return ExistingTaskReservation(task=_to_read(retained))

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
        result: TaskResult,
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
        try:
            needs_review = await self._failure_requires_review(task_id, lease_token)
        except Exception, asyncio.CancelledError:
            await self._db.rollback()
            raise
        if needs_review:
            return await self.require_review(task_id, lease_token=lease_token)
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

    async def _failure_requires_review(self, task_id: TaskId, lease_token: uuid.UUID) -> bool:
        phase_value = (
            await self._db.execute(
                select(_failure_checkpoint_phase())
                .where(
                    Task.id == task_id,
                    Task.status == TaskStatus.RUNNING,
                    Task.lease_token == lease_token,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if phase_value is None:
            raise TaskLeaseLostError
        phase = _FailureCheckpointPhase(phase_value)
        match phase:
            case _FailureCheckpointPhase.ABSENT:
                return False
            case _FailureCheckpointPhase.UNCERTAIN:
                return True
            case _FailureCheckpointPhase.ENCRYPTED_COPY:
                checkpoint = (
                    await self._db.execute(select(Task.checkpoint).where(Task.id == task_id))
                ).scalar_one()
                return _checkpoint_requires_review(_TaskJsonObject.model_validate(checkpoint))
            case _:
                assert_never(phase)

    @override
    async def require_review(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        now = datetime.now(UTC)
        return await self._owned_lease_update(
            task_id,
            lease_token,
            values=_review_values(now),
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
                    _visible_task(),
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
                    _visible_task(),
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
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        task = (
            await self._db.execute(
                select(Task)
                .options(
                    load_only(
                        *_PUBLIC_TASK_COLUMNS, Task.owner_user_id, Task.payload, raiseload=True
                    )
                )
                .where(Task.id == task_id, _visible_task())
            )
        ).scalar_one_or_none()
        if task is None or task.kind != TaskKind.FILE_SMART_CREATE:
            raise TaskNotFoundError
        public_task = _to_read(task)
        if not isinstance(public_task, PendingTaskRead | RunningTaskRead):
            return SettledSmartCreateTaskRecord.model_validate(
                {"owner_user_id": task.owner_user_id, "task": public_task}
            )
        return ActiveSmartCreateTaskRecord.model_validate(
            {
                "owner_user_id": task.owner_user_id,
                "task": public_task,
                "payload": task.payload,
            }
        )

    @override
    async def begin_smart_create(
        self, task_id: TaskId, *, lease_token: uuid.UUID
    ) -> SmartCreateDispatch:
        owned = (
            Task.id == task_id,
            Task.kind == TaskKind.FILE_SMART_CREATE,
            Task.status == TaskStatus.RUNNING,
            Task.lease_token == lease_token,
            Task.lease_expires_at > func.now(),
        )
        started = (
            await self._db.execute(
                update(Task)
                .where(*owned, Task.checkpoint.is_(None))
                .values(
                    checkpoint=SmartCreateCheckpoint().model_dump(mode="json"),
                    checkpoint_generation=Task.checkpoint_generation + 1,
                    updated_at=func.now(),
                )
                .returning(Task.id)
                .execution_options(synchronize_session=False)
            )
        ).scalar_one_or_none()
        if started is not None:
            await self._db.commit()
            return SmartCreateDispatch.STARTED
        checkpoint = (
            await self._db.execute(select(Task.checkpoint).where(*owned))
        ).scalar_one_or_none()
        await self._db.rollback()
        if checkpoint is None:
            raise TaskLeaseLostError
        SmartCreateCheckpoint.model_validate(checkpoint)
        return SmartCreateDispatch.ALREADY_ATTEMPTED

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
        if checkpoint is not None and checkpoint.kind == "in_place":
            values["resource_id"] = checkpoint.document_id
        elif checkpoint.kind != "model_planning" and checkpoint.output is not None:
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
        try:
            generation = (await self._db.execute(statement)).scalar_one_or_none()
            if generation is not None:
                await self._db.commit()
        except BaseException:
            # Cancellation or an ambiguous commit must leave the session usable
            # for the owner's bounded defer/failure path. A new delivery reloads
            # the durable generation before doing any further work.
            await self._db.rollback()
            raise
        if generation is None:
            await self._db.rollback()
            raise TaskLeaseLostError
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
                .where(Task.id == task_id, _visible_task())
            )
        ).scalar_one_or_none()
        if task is None or task.kind != TaskKind.FILE_DOCUMENT_TRANSFORM:
            raise TaskNotFoundError
        public_task = _to_read(task)
        if public_task.kind is not TaskKind.FILE_DOCUMENT_TRANSFORM:
            raise TaskNotFoundError
        match public_task:
            case NeedsReviewTaskRead():
                # Review never decrypts or rewrites the original operator evidence.
                return ReviewDocumentTransformTaskRecord.model_validate(
                    {"owner_user_id": task.owner_user_id, "task": public_task.model_dump()}
                )
            case _:
                pass
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
        record_type = (
            ActiveDocumentTransformTaskRecord
            if isinstance(public_task, PendingTaskRead | RunningTaskRead)
            else TerminalDocumentTransformTaskRecord
        )
        return record_type.model_validate(
            {
                "owner_user_id": task.owner_user_id,
                "task": public_task,
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
                    _visible_task(),
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
                    Task.result["needs_review"].astext == "false",
                    Task.request_hash == request_hash,
                    Task.finished_at.is_not(None),
                    Task.expires_at > func.now(),
                    Task.result["pipeline_version"].astext.in_(CURRENT_PIPELINE_VERSIONS),
                    Task.result["brand_profile_sha256"].astext == CURRENT_BRAND_MANIFEST.sha256,
                    _terminal_catalog_matches(),
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
                    Task.result["needs_review"].astext == "false",
                    Task.request_hash == request_hash,
                    Task.finished_at.is_not(None),
                    Task.expires_at > func.clock_timestamp(),
                    Task.result["pipeline_version"].astext.in_(CURRENT_PIPELINE_VERSIONS),
                    Task.result["brand_profile_sha256"].astext == CURRENT_BRAND_MANIFEST.sha256,
                    _terminal_catalog_matches(),
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
        return ExistingTaskReservation(task=terminal_read)

    @override
    async def get_admission(
        self, owner_user_id: UserId, kind: TaskKind, idempotency_key: IdempotencyKey
    ) -> TaskAdmission:
        # Project only public admission identity in one retained-row read. Never
        # load/decrypt payloads or checkpoints, nor renew retention or dispatch.
        row = (
            await self._db.execute(
                select(Task.id, Task.status).where(
                    Task.owner_user_id == owner_user_id,
                    Task.kind == kind,
                    _visible_task(),
                    Task.id.in_(_task_ids_for_key(owner_user_id, kind, idempotency_key)),
                )
            )
        ).one_or_none()
        if row is None:
            raise TaskNotFoundError
        return TaskAdmission(task_id=row.id, status=row.status)

    async def _owned_lease_update(
        self,
        task_id: TaskId,
        lease_token: uuid.UUID,
        *,
        values: _OwnedLeaseValues,
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
        try:
            task = (await self._db.execute(statement)).scalar_one()
        except NoResultFound:
            await self._db.rollback()
            raise TaskLeaseLostError from None
        except Exception, asyncio.CancelledError:
            await self._db.rollback()
            raise
        try:
            # Validate the prospective state while the transaction can still roll back.
            # A wrong result kind must never become a committed successful task.
            task_read = _to_read(task)
            if header_result is not None and header_result.header_identity is not None:
                await self._persist_header_identity(task, header_result)
        except Exception, asyncio.CancelledError:
            await self._db.rollback()
            raise
        await self._db.commit()
        return task_read

    async def _persist_header_identity(self, task: Task, result: DocumentTransformResult) -> None:
        try:
            checkpoint = (
                _deserialize_checkpoint(task.checkpoint) if task.checkpoint is not None else None
            )
        except (InvalidToken, TypeError, ValueError) as exc:
            raise OutputConflictError from exc
        evidence = result.header_identity
        if (
            not isinstance(checkpoint, CopyPlan)
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
        if not _terminal_result_matches(finalized, result):
            raise OutputConflictError
        if finalized.output_revision_id is None or finalized.output.file_id != evidence.file_id:
            raise OutputConflictError
        # Alias receipts are added after provider finalization. Hash the exact
        # pre-alias checkpoint result whose Drive completion was read back.
        result_digest = hashlib.sha256(
            json.dumps(
                _serialize(finalized),
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


def _terminal_catalog_matches() -> ColumnElement[bool]:
    return or_(
        Task.result["mode"].astext == TransformMode.FORMAT.value,
        and_(
            Task.result["mode"].astext.in_((TransformMode.UNSLOP.value, TransformMode.BOTH.value)),
            Task.result["tool_versions"]["unslop_catalog_sha256"].astext == UNSLOP_CATALOG_SHA256,
        ),
    )


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


def _serialize(model: BaseModel) -> dict[str, JsonValue]:
    serialized = model.model_dump(mode="json")
    match model:
        case DocumentTransformRequest():
            # Preserve pre-destination idempotency hashes for every legacy request shape.
            if model.copy_destination_folder_id is None:
                serialized.pop("copy_destination_folder_id", None)
            # Preserve the pre-output_strategy payload and idempotency hash for legacy copy calls.
            if model.output_strategy is OutputStrategy.COPY:
                serialized.pop("output_strategy", None)
        case _:
            pass
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


def _terminal_payload() -> ColumnElement[dict[str, JsonValue]]:
    return case(
        (
            Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
            literal(_TERMINAL_REDACTED_PAYLOAD, type_=JSONB),
        ),
        else_=Task.payload,
    )


def _serialize_checkpoint(checkpoint: DocumentTransformCheckpoint) -> dict[str, JsonValue]:
    serialized = checkpoint.model_dump(mode="json")
    if isinstance(
        checkpoint, CopyPlan | WordCopyPlan | PresentationCopyPlan | ModelPlanningCheckpoint
    ):
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
    envelope = _StoredCheckpointEnvelope.model_validate(raw).root
    match envelope:
        case _EncryptedCopyEnvelope():
            payload = _TaskJsonObject.model_validate_json(
                decrypt_cache(envelope.checkpoint_ciphertext)
            ).root
            payload = {
                field: value
                for field, value in payload.items()
                if value is not None
                or field
                not in {"qa_destination_permission", "qa_permission_evidence", "qa_quarantine"}
            }
            match envelope.kind:
                case "presentation_copy":
                    return PresentationCheckpointPayload.model_validate(payload).root
                case "word_copy":
                    return WordCheckpointPayload.model_validate(payload).root
                case "copy":
                    return CopyCheckpointPayload.model_validate(payload).root
                case "model_planning":
                    return ModelPlanningCheckpoint.model_validate(payload)
                case _:
                    assert_never(envelope.kind)
        case _EncryptedInPlaceEnvelope():
            metadata = {
                **envelope.metadata,
                "kind": envelope.kind,
                "version": envelope.version,
            }
            plan = _TaskJsonObject.model_validate_json(decrypt_cache(envelope.plan_ciphertext)).root
            if set(plan) != _CHECKPOINT_PLAN_FIELDS:
                msg = "Document transform checkpoint plan is invalid"
                raise ValueError(msg)
            metadata.update(plan)
            return InPlaceCheckpointPayload.model_validate(metadata).root
        case _:
            assert_never(envelope)


def _persisted_error_detail(error_detail: str | None) -> str | None:
    if error_detail is None:
        return None
    return _TASK_FAILURE_DETAIL[:_MAX_ERROR_DETAIL_LENGTH]


def _failure_checkpoint_phase() -> ColumnElement[str]:
    encrypted = Task.checkpoint[_COPY_CHECKPOINT_CIPHERTEXT_FIELD]
    return case(
        (Task.checkpoint.is_(None), _FailureCheckpointPhase.ABSENT.value),
        (
            and_(
                Task.kind == TaskKind.FILE_DOCUMENT_TRANSFORM,
                Task.checkpoint["kind"].astext.in_(
                    ("copy", "word_copy", "presentation_copy", "model_planning")
                ),
                or_(
                    Task.checkpoint["kind"].astext != "model_planning",
                    Task.resource_id.is_(None),
                ),
                func.jsonb_typeof(encrypted) == "string",
                func.octet_length(encrypted.astext) <= 2 * _MAX_CHECKPOINT_PLAN_BYTES,
            ),
            _FailureCheckpointPhase.ENCRYPTED_COPY.value,
        ),
        else_=_FailureCheckpointPhase.UNCERTAIN.value,
    )


def _checkpoint_requires_review(raw: _TaskJsonObject) -> bool:
    try:
        checkpoint = _deserialize_checkpoint(raw.root)
    except InvalidToken, TypeError, ValueError:
        return True
    return not isinstance(
        checkpoint,
        PlannedCopyCheckpoint
        | PlannedWordCheckpoint
        | PlannedPresentationCheckpoint
        | ModelPlanningCheckpoint,
    )


def _transform_slot_phase(now: ColumnElement[datetime]) -> ColumnElement[str]:
    return case(
        (Task.status == TaskStatus.NEEDS_REVIEW, _TransformSlotPhase.REVIEW.value),
        (Task.expires_at > now, _TransformSlotPhase.LIVE.value),
        (Task.status == TaskStatus.PENDING, _TransformSlotPhase.EXPIRED_PENDING.value),
        (
            Task.lease_expires_at > now - _LEASE_RETENTION_GRACE,
            _TransformSlotPhase.RETAINED_LEASE.value,
        ),
        else_=_TransformSlotPhase.ABANDONED.value,
    )


def _request_hash(payload: dict[str, JsonValue]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class _ReviewValues(TypedDict):
    status: Literal[TaskStatus.NEEDS_REVIEW]
    error_code: Literal["external_effect_uncertain"]
    error_detail: str
    lease_token: None
    lease_expires_at: None
    finished_at: datetime | ColumnElement[datetime]
    updated_at: datetime | ColumnElement[datetime]


class _DeferredLeaseValues(TypedDict):
    lease_expires_at: ColumnElement[datetime]
    updated_at: ColumnElement[datetime]


class _TerminalLeaseValues(TypedDict):
    payload: ColumnElement[dict[str, JsonValue]]
    lease_token: None
    lease_expires_at: None
    finished_at: datetime
    updated_at: datetime


class _SucceededLeaseValues(_TerminalLeaseValues):
    status: Literal[TaskStatus.SUCCEEDED]
    result: dict[str, JsonValue]


class _FailedLeaseValues(_TerminalLeaseValues):
    status: Literal[TaskStatus.FAILED]
    error_code: str
    error_detail: str | None


type _OwnedLeaseValues = (
    _DeferredLeaseValues | _SucceededLeaseValues | _FailedLeaseValues | _ReviewValues
)


def _review_values(now: datetime | ColumnElement[datetime]) -> _ReviewValues:
    return {
        "status": TaskStatus.NEEDS_REVIEW,
        "error_code": "external_effect_uncertain",
        "error_detail": "The external action could not be confirmed; review it before retrying",
        "lease_token": None,
        "lease_expires_at": None,
        "finished_at": now,
        "updated_at": now,
    }


def _visible_task() -> ColumnElement[bool]:
    return or_(Task.expires_at > func.now(), Task.status == TaskStatus.NEEDS_REVIEW)


def _to_read(task: Task) -> TaskRead:
    result = _TaskReadPayload.model_validate(task, from_attributes=True).root
    match result:
        case RunningTaskRead():
            expiry = task.lease_expires_at
            if expiry is None:
                msg = "Running task has no lease expiry"
                raise ValueError(msg)
            return result.model_validate(
                result.model_dump()
                | {"execution_state": "retrying" if expiry <= datetime.now(UTC) else "running"}
            )
        case _:
            return result
