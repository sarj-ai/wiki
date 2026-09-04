from __future__ import annotations

from time import monotonic
from typing import Annotated, Protocol
import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status

from app.auth.dependencies import get_current_user
from app.config import settings
from app.document_transform.schemas import (
    DocumentTransformCapabilities,
    DocumentTransformRequest,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
)
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.files.dependencies import files_service_dep
from app.files.errors import FileNotInScopeError, FilesError
from app.files.schemas import FileResponse
from app.identifiers import FileId, IdempotencyKey
from app.models.user import User
from app.openapi import authentication_responses
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import PendingTaskRead, RunningTaskRead, TaskKind, TaskRead
from app.tasks.service import TaskService


router = APIRouter(
    prefix="/document-transforms",
    tags=["document-transforms"],
    responses=authentication_responses(),
)


class DestinationFileReader(Protocol):
    async def get(self, file_id: FileId) -> FileResponse: ...


@router.get(
    "/capabilities",
    operation_id="get_document_transform_capabilities",
    status_code=status.HTTP_200_OK,
)
async def get_document_transform_capabilities(
    _user: Annotated[User, Depends(get_current_user)],
) -> DocumentTransformCapabilities:
    return DocumentTransformCapabilities(in_place=settings.DOCUMENT_TRANSFORM_IN_PLACE_ENABLED)


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_document_transform",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "In-place transformation is limited to native Google Docs"
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "In-place document transformation is disabled"
        },
    },
)
async def create_document_transform(
    *,
    payload: Annotated[DocumentTransformRequest, Body()],
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    enqueuer: Annotated[TaskEnqueuer, Depends(task_enqueuer_dep)],
    files: Annotated[DestinationFileReader, Depends(files_service_dep)],
) -> TaskRead:
    if (
        payload.output_strategy is OutputStrategy.IN_PLACE
        and not settings.DOCUMENT_TRANSFORM_IN_PLACE_ENABLED
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="In-place document transformation is disabled",
        )
    started = monotonic()
    reservation = await tasks.create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=user.id,
        idempotency_key=IdempotencyKey(idempotency_key),
        payload=payload,
    )
    if reservation.created and isinstance(payload.source, MarkdownSource):
        try:
            destination = await files.get(payload.source.destination_folder_id)
            if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
                message = "Destination does not accept new files"
                raise FileNotInScopeError(message)
        except FilesError:
            await tasks.discard_pending(reservation.task.id, owner_user_id=user.id)
            raise
    if (
        reservation.created
        and payload.output_strategy is OutputStrategy.IN_PLACE
        and isinstance(payload.source, DriveFileSource)
    ):
        try:
            source = await files.get(payload.source.file_id)
        except FilesError:
            await tasks.discard_pending(reservation.task.id, owner_user_id=user.id)
            raise
        if source.mime_type != MIME_DOC:
            await tasks.discard_pending(reservation.task.id, owner_user_id=user.id)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="In-place transformation is available only for native Google Docs",
            )
    if isinstance(reservation.task, PendingTaskRead | RunningTaskRead):
        await enqueuer.enqueue_document_transform(reservation.task.id)
    response.headers["Idempotency-Replayed"] = str(not reservation.created).lower()
    response.headers["Retry-After"] = "1"
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return reservation.task
