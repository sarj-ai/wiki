from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    QaDocumentTransformRequest,
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
    kind: TaskKind
    created_at: datetime


class PendingTaskRead(_TaskReadBase):
    status: Literal[TaskStatus.PENDING]
    result: None
    resource_id: None
    error_code: None
    error_detail: None
    attempt_count: Literal[0]
    started_at: None
    finished_at: None


class RunningTaskRead(_TaskReadBase):
    status: Literal[TaskStatus.RUNNING]
    result: None
    resource_id: FileId | None
    error_code: None
    error_detail: None
    attempt_count: PositiveInt
    started_at: datetime
    finished_at: None


class SucceededTaskRead(_TaskReadBase):
    status: Literal[TaskStatus.SUCCEEDED]
    result: SmartCreateResult | DocumentTransformResult
    resource_id: FileId | None
    error_code: None
    error_detail: None
    attempt_count: PositiveInt
    started_at: datetime
    finished_at: datetime


class UnverifiedTaskOutput(BaseModel):
    file_id: FileId


class FailedTaskRead(_TaskReadBase):
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
    payload: DocumentTransformRequest | QaDocumentTransformRequest | None
    checkpoint: DocumentTransformCheckpoint | None = None
    checkpoint_generation: int = Field(default=0, ge=0)


class TaskDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: TaskId
