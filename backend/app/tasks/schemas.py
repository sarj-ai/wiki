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


class DocumentTransformAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: TaskId
    status: TaskStatus


class _TaskReadBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: TaskId
    created_at: datetime


class PendingTaskRead(_TaskReadBase):
    kind: TaskKind
    status: Literal[TaskStatus.PENDING]
    result: None
    resource_id: None
    error_code: None
    error_detail: None
    attempt_count: Literal[0]
    started_at: None
    finished_at: None


class RunningTaskRead(_TaskReadBase):
    kind: TaskKind
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


class FailedTaskRead(_TaskReadBase):
    kind: TaskKind
    status: Literal[TaskStatus.FAILED]
    result: None
    resource_id: FileId | None
    error_code: str
    error_detail: str | None
    unverified_output: UnverifiedTaskOutput | None = None
    attempt_count: PositiveInt
    started_at: datetime
    finished_at: datetime


class CancelledTaskRead(_TaskReadBase):
    kind: TaskKind
    status: Literal[TaskStatus.CANCELLED]
    result: None
    resource_id: None
    error_code: None
    error_detail: None
    attempt_count: Literal[0]
    started_at: None
    finished_at: datetime


TaskRead = Annotated[
    PendingTaskRead | RunningTaskRead | SucceededTaskRead | FailedTaskRead | CancelledTaskRead,
    Field(discriminator="status"),
]


class TaskReservation(BaseModel):
    task: TaskRead
    created: bool


class TaskClaim(BaseModel):
    task: RunningTaskRead
    lease_token: uuid.UUID


class SmartCreateTaskRecord(BaseModel):
    owner_user_id: uuid.UUID
    task: TaskRead
    payload: SmartFileCreate


class DocumentTransformTaskRecord(BaseModel):
    owner_user_id: uuid.UUID
    task: TaskRead
    payload: DocumentTransformRequest | None
    checkpoint: DocumentTransformCheckpoint | None = None
    checkpoint_generation: int = Field(default=0, ge=0)


class TaskDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: TaskId
