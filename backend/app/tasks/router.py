from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Path, Response, status

from app.auth.dependencies import get_current_user
from app.document_transform.schemas import (
    CopyCheckpoint,
    DriveFileSource,
    InPlaceCheckpoint,
    OutputStrategy,
    WordCopyCheckpoint,
)
from app.files.dependencies import files_service_dep
from app.files.errors import FilesError
from app.files.schemas import FileResponse
from app.identifiers import FileId, TaskId, UserId
from app.models.user import User
from app.openapi import authentication_responses
from app.tasks.dependencies import task_service_dep
from app.tasks.schemas import (
    DocumentTransformTaskRecord,
    FailedTaskRead,
    PendingTaskRead,
    RunningTaskRead,
    TaskKind,
    TaskRead,
    UnverifiedTaskOutput,
)


class TaskReader(Protocol):
    async def get_for_owner(self, task_id: TaskId, owner_user_id: UserId) -> TaskRead: ...

    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord: ...


class OutputReader(Protocol):
    async def get(self, file_id: FileId) -> FileResponse: ...


router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user)],
    responses=authentication_responses(),
)


@router.get(
    "/{task_id}",
    status_code=status.HTTP_200_OK,
    operation_id="get_task",
    responses={
        200: {
            "headers": {"Retry-After": {"schema": {"type": "integer"}}},
            "description": "Current task state.",
        }
    },
)
async def get_task(
    task_id: Annotated[TaskId, Path()],
    service: Annotated[TaskReader, Depends(task_service_dep)],
    user: Annotated[User, Depends(get_current_user)],
    response: Response,
    files: Annotated[OutputReader, Depends(files_service_dep)],
) -> TaskRead:
    task = await service.get_for_owner(task_id, user.id)
    if isinstance(task, FailedTaskRead) and task.kind is TaskKind.FILE_DOCUMENT_TRANSFORM:
        task = await _with_unverified_output(task, service, user, files)
    if isinstance(task, PendingTaskRead | RunningTaskRead):
        response.headers["Retry-After"] = "1"
    return task


async def _with_unverified_output(
    task: FailedTaskRead, service: TaskReader, user: User, files: OutputReader
) -> FailedTaskRead:
    record = await service.load_document_transform(task.id)
    checkpoint = record.checkpoint
    payload = record.payload
    if record.owner_user_id != user.id:
        return task
    if task.error_code == "in_place_verification_failed":
        if not isinstance(checkpoint, InPlaceCheckpoint):
            return task
        payload_matches_original = payload is None or (
            payload.output_strategy is OutputStrategy.IN_PLACE
            and isinstance(payload.source, DriveFileSource)
            and payload.source.file_id == checkpoint.document_id
        )
        if task.resource_id != checkpoint.document_id or not payload_matches_original:
            return task
        output_id = checkpoint.document_id
    else:
        if (
            (payload is not None and payload.output_strategy is not OutputStrategy.COPY)
            or not isinstance(checkpoint, CopyCheckpoint | WordCopyCheckpoint)
            or checkpoint.output is None
        ):
            return task
        output_id = checkpoint.output.file_id
        if (
            payload is not None
            and isinstance(payload.source, DriveFileSource)
            and output_id == payload.source.file_id
        ):
            return task
    try:
        accessible = await files.get(output_id)
    except FilesError:
        return task
    if accessible.id != output_id:
        return task
    return task.model_copy(update={"unverified_output": UnverifiedTaskOutput(file_id=output_id)})
