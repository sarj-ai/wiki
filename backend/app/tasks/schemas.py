from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
import uuid

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
)
from app.identifiers import FileId, TaskId
from app.smart_create.schemas import SmartCreateResult, SmartFileCreate


class TaskKind(StrEnum):
    FILE_SMART_CREATE = "file.smart_create"
    FILE_DOCUMENT_TRANSFORM = "file.document_transform"


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


class TaskAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: TaskId
    status: TaskStatus


class _TaskReadBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: TaskId
    created_at: datetime


class PendingTaskRead[TaskKindType: TaskKind = TaskKind](_TaskReadBase):
    kind: TaskKindType
    status: Literal[TaskStatus.PENDING]
    result: None
    resource_id: None
    error_code: None
    error_detail: None
    attempt_count: Literal[0]
    started_at: None
    finished_at: None


class RunningTaskRead[TaskKindType: TaskKind = TaskKind](_TaskReadBase):
    kind: TaskKindType
    status: Literal[TaskStatus.RUNNING]
    execution_state: Literal["running", "retrying"] = "running"
    result: None
    resource_id: FileId | None
    error_code: None
    error_detail: None
    attempt_count: PositiveInt
    started_at: datetime
    finished_at: None


class _SucceededTaskReadBase(_TaskReadBase):
    status: Literal[TaskStatus.SUCCEEDED]
    resource_id: FileId | None
    error_code: None
    error_detail: None
    attempt_count: PositiveInt
    started_at: datetime
    finished_at: datetime


class SmartCreateSucceededTaskRead(_SucceededTaskReadBase):
    kind: Literal[TaskKind.FILE_SMART_CREATE]
    result: SmartCreateResult

    @model_validator(mode="after")
    def matching_task_id(self) -> Self:
        if self.result.task_id != self.id:
            msg = "Task result refers to a different task"
            raise ValueError(msg)
        return self


class DocumentTransformSucceededTaskRead(_SucceededTaskReadBase):
    kind: Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]
    result: DocumentTransformResult

    @model_validator(mode="after")
    def matching_task_id(self) -> Self:
        if self.result.operation_id != self.id:
            msg = "Task result refers to a different operation"
            raise ValueError(msg)
        return self


type SucceededTaskRead = Annotated[
    SmartCreateSucceededTaskRead | DocumentTransformSucceededTaskRead,
    Field(discriminator="kind"),
]

type TaskResult = SmartCreateResult | DocumentTransformResult


class UnverifiedTaskOutput(BaseModel):
    file_id: FileId


class FailedTaskRead[TaskKindType: TaskKind = TaskKind](_TaskReadBase):
    kind: TaskKindType
    status: Literal[TaskStatus.FAILED]
    result: None
    resource_id: FileId | None
    error_code: str
    error_detail: str | None
    unverified_output: UnverifiedTaskOutput | None = None
    attempt_count: PositiveInt
    started_at: datetime
    finished_at: datetime


class CancelledTaskRead[TaskKindType: TaskKind = TaskKind](_TaskReadBase):
    kind: TaskKindType
    status: Literal[TaskStatus.CANCELLED]
    result: None
    resource_id: None
    error_code: None
    error_detail: None
    attempt_count: Literal[0]
    started_at: None
    finished_at: datetime


class NeedsReviewTaskRead[TaskKindType: TaskKind = TaskKind](_TaskReadBase):
    kind: TaskKindType
    status: Literal[TaskStatus.NEEDS_REVIEW]
    result: None
    resource_id: FileId | None
    error_code: Literal["external_effect_uncertain"]
    error_detail: Annotated[str, Field(min_length=1)]
    attempt_count: PositiveInt
    started_at: datetime
    finished_at: datetime


type SmartCreateTaskRead = Annotated[
    PendingTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
    | RunningTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
    | SmartCreateSucceededTaskRead
    | FailedTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
    | CancelledTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
    | NeedsReviewTaskRead[Literal[TaskKind.FILE_SMART_CREATE]],
    Field(discriminator="status"),
]


type DocumentTransformTaskRead = Annotated[
    PendingTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]]
    | RunningTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]]
    | DocumentTransformSucceededTaskRead
    | FailedTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]]
    | CancelledTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]]
    | NeedsReviewTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]],
    Field(discriminator="status"),
]


TaskRead = Annotated[
    SmartCreateTaskRead | DocumentTransformTaskRead,
    Field(discriminator="kind"),
]


class NewTaskReservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: Annotated[
        PendingTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
        | PendingTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]],
        Field(discriminator="kind"),
    ]
    created: Literal[True] = True


class ExistingTaskReservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: TaskRead
    created: Literal[False] = False


type TaskReservation = NewTaskReservation | ExistingTaskReservation


class TaskClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: Annotated[
        RunningTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
        | RunningTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]],
        Field(discriminator="kind"),
    ]
    lease_token: uuid.UUID


class ActiveSmartCreateTaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: uuid.UUID
    task: Annotated[
        PendingTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
        | RunningTaskRead[Literal[TaskKind.FILE_SMART_CREATE]],
        Field(discriminator="status"),
    ]
    payload: SmartFileCreate


class SettledSmartCreateTaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: uuid.UUID
    task: Annotated[
        SmartCreateSucceededTaskRead
        | FailedTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
        | CancelledTaskRead[Literal[TaskKind.FILE_SMART_CREATE]]
        | NeedsReviewTaskRead[Literal[TaskKind.FILE_SMART_CREATE]],
        Field(discriminator="status"),
    ]


type SmartCreateTaskRecord = ActiveSmartCreateTaskRecord | SettledSmartCreateTaskRecord


class SmartCreateDispatch(StrEnum):
    STARTED = "started"
    ALREADY_ATTEMPTED = "already_attempted"


class SmartCreateCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    kind: Literal["smart_create"] = "smart_create"
    stage: Literal["creating"] = "creating"


class _DocumentTransformTaskProgress(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: uuid.UUID
    checkpoint: DocumentTransformCheckpoint | None = None
    checkpoint_generation: int = Field(default=0, ge=0)


class ActiveDocumentTransformTaskRecord(_DocumentTransformTaskProgress):
    task: Annotated[
        PendingTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]]
        | RunningTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]],
        Field(discriminator="status"),
    ]
    payload: DocumentTransformRequest


class TerminalDocumentTransformTaskRecord(_DocumentTransformTaskProgress):
    task: Annotated[
        DocumentTransformSucceededTaskRead
        | FailedTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]]
        | CancelledTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]],
        Field(discriminator="status"),
    ]
    payload: DocumentTransformRequest | None


class ReviewDocumentTransformTaskRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner_user_id: uuid.UUID
    task: NeedsReviewTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]]


type DocumentTransformTaskRecord = (
    ActiveDocumentTransformTaskRecord
    | TerminalDocumentTransformTaskRecord
    | ReviewDocumentTransformTaskRecord
)


class TaskDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: TaskId
