from __future__ import annotations

from time import monotonic
from typing import Annotated, Protocol
import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status

from app.auth.dependencies import get_current_user
from app.config import settings
from app.document_transform.docx_renderer import validate_markdown_import
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.ir import parse_markdown
from app.document_transform.schemas import (
    DocumentTransformCapabilities,
    DocumentTransformRequest,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
)
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.files.dependencies import files_service_dep
from app.files.errors import FileNotInScopeError
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
        status.HTTP_413_CONTENT_TOO_LARGE: {
            "description": "Markdown input exceeds a bounded conversion limit"
        },
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
    if isinstance(payload.source, MarkdownSource):
        try:
            parse_markdown(payload.source.content, title=payload.source.name)
            validate_markdown_import(payload.source.content, title=payload.source.name)
        except SourceTooLargeError as exc:
            raise HTTPException(status_code=413, detail=exc.public_detail) from exc
        except UnsupportedSourceError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.public_detail,
            ) from exc
    started = monotonic()

    async def admit() -> None:
        await _validate_admission(payload, files)

    reservation = await tasks.create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=user.id,
        idempotency_key=IdempotencyKey(idempotency_key),
        payload=payload,
        admit=admit,
    )
    if isinstance(reservation.task, PendingTaskRead | RunningTaskRead):
        await enqueuer.enqueue_document_transform(reservation.task.id)
    response.headers["Idempotency-Replayed"] = str(not reservation.created).lower()
    response.headers["Retry-After"] = "1"
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return reservation.task


async def _validate_admission(
    payload: DocumentTransformRequest, files: DestinationFileReader
) -> None:
    if isinstance(payload.source, MarkdownSource):
        destination = await files.get(payload.source.destination_folder_id)
        if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
            message = "Destination does not accept new files"
            raise FileNotInScopeError(message)
    if isinstance(payload.source, DriveFileSource):
        source = await files.get(payload.source.file_id)
        if payload.output_strategy is OutputStrategy.IN_PLACE and source.mime_type != MIME_DOC:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="In-place transformation is available only for native Google Docs",
            )
