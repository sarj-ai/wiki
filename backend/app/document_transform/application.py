from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import uuid

from fastapi import HTTPException, status

from app.config import settings
from app.document_transform.docx_renderer import validate_markdown_import
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.ir import parse_markdown
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
)
from app.document_transform.word_gateway import WORD_MIME
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import FileId, IdempotencyKey
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import PendingTaskRead, RunningTaskRead, TaskKind, TaskRead
from app.tasks.service import TaskService


class DestinationFileReader(Protocol):
    async def get(self, file_id: FileId) -> FileResponse: ...


@dataclass(frozen=True)
class TransformSubmission:
    task: TaskRead
    replayed: bool
    retry_after: int = 1


async def submit_document_transform(
    *,
    payload: DocumentTransformRequest,
    owner_user_id: uuid.UUID,
    idempotency_key: IdempotencyKey,
    tasks: TaskService,
    enqueuer: TaskEnqueuer,
    files: DestinationFileReader,
) -> TransformSubmission:
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

    async def admit() -> None:
        await validate_transform_admission(payload, files)

    reservation = await tasks.create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=owner_user_id,
        idempotency_key=idempotency_key,
        payload=payload,
        admit=admit,
    )
    if isinstance(reservation.task, PendingTaskRead | RunningTaskRead):
        await enqueuer.enqueue_document_transform(reservation.task.id)
    return TransformSubmission(task=reservation.task, replayed=not reservation.created)


async def validate_transform_admission(
    payload: DocumentTransformRequest, files: DestinationFileReader
) -> None:
    if isinstance(payload.source, MarkdownSource):
        destination = await files.get(payload.source.destination_folder_id)
        if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
            message = "Destination does not accept new files"
            raise FileNotInScopeError(message)
    if isinstance(payload.source, DriveFileSource):
        source = await files.get(payload.source.file_id)
        if payload.copy_destination_folder_id is not None:
            if source.mime_type != MIME_DOC:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="A copy destination is available only for native Google Docs",
                )
            destination = await files.get(payload.copy_destination_folder_id)
            if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
                message = "Destination does not accept new files"
                raise FileNotInScopeError(message)
        if source.mime_type == WORD_MIME and (
            payload.mode.value != "format" or payload.output_strategy is not OutputStrategy.COPY
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Word documents currently support formatting-only copies; the original Word file stays unchanged",
            )
        if payload.output_strategy is OutputStrategy.IN_PLACE and source.mime_type != MIME_DOC:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="In-place transformation is available only for native Google Docs",
            )
