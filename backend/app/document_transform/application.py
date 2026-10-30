from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, status

from app.config import settings
from app.document_transform.docx_renderer import validate_markdown_import
from app.document_transform.errors import (
    DocumentGatewayError,
    SourceChangedError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.gcs_uploads import (
    UploadNotReadyError,
    UploadUnavailableError,
    load_markdown_upload,
)
from app.document_transform.ir import parse_markdown
from app.document_transform.schemas import (
    CurrentDocumentPolish,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
    TransformMode,
)
from app.document_transform.word_gateway import WORD_MIME
from app.drive.retry import ProviderDeadlineError
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.files.errors import DriveUpstreamError, FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import DocsRevisionId, FileId, IdempotencyKey, UserId
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import (
    PendingTaskRead,
    RunningTaskRead,
    SucceededTaskRead,
    TaskKind,
    TaskRead,
)
from app.tasks.service import TaskService


class DestinationFileReader(Protocol):
    async def get(self, file_id: FileId) -> FileResponse: ...


class CurrentDocumentRevisionReader(Protocol):
    async def current_revision(self, file_id: FileId) -> DocsRevisionId: ...


@dataclass(frozen=True)
class TransformSubmission:
    task: TaskRead
    replayed: bool
    retry_after: int = 1


SOURCE_CHANGED_ERROR_CODE = "document_transform_source_changed"


async def get_current_document_polish(
    *,
    file_id: FileId,
    mode: TransformMode,
    owner_user_id: UserId,
    tasks: TaskService,
    files: DestinationFileReader,
    revisions: CurrentDocumentRevisionReader,
) -> CurrentDocumentPolish:
    source = await files.get(file_id)
    if source.mime_type != MIME_DOC:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Current in-place polish is available only for native Google Docs",
        )
    try:
        revision_id = await revisions.current_revision(file_id)
    except (DocumentGatewayError, ProviderDeadlineError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The latest Google Docs revision could not be verified",
        ) from exc
    result = await tasks.get_current_in_place_result(owner_user_id, file_id, mode, revision_id)
    return CurrentDocumentPolish(result=result)


async def submit_document_transform(
    *,
    payload: DocumentTransformRequest,
    owner_user_id: UserId,
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
    if isinstance(payload.source, MarkdownSource) and payload.source.kind == "markdown":
        try:
            await asyncio.to_thread(_validate_markdown_source, payload.source)
        except SourceTooLargeError as exc:
            raise HTTPException(status_code=413, detail=exc.public_detail) from exc
        except UnsupportedSourceError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.public_detail,
            ) from exc

    authorized_source: FileResponse | None = None

    async def admit() -> None:
        nonlocal authorized_source
        authorized_source = await validate_transform_authorization(payload, files)
        if (
            isinstance(payload.source, MarkdownSource)
            and payload.source.kind == "uploaded_markdown"
        ):
            try:
                markdown = await load_markdown_upload(owner_user_id, payload.source)
                await asyncio.to_thread(_validate_markdown_source, markdown)
            except UploadNotReadyError as exc:
                raise HTTPException(status_code=422, detail="Upload is not complete") from exc
            except UploadUnavailableError as exc:
                raise HTTPException(status_code=503, detail="Upload could not be verified") from exc
            except SourceTooLargeError as exc:
                raise HTTPException(status_code=413, detail=exc.public_detail) from exc
            except UnsupportedSourceError as exc:
                raise HTTPException(status_code=422, detail=exc.public_detail) from exc

    def admit_new() -> None:
        validate_transform_freshness(payload, authorized_source)

    async def admit_terminal_replay(task: TaskRead) -> bool:
        return await retained_transform_output_available(task, files)

    reservation = await tasks.create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=owner_user_id,
        idempotency_key=idempotency_key,
        payload=payload,
        admit=admit,
        admit_new=admit_new,
        admit_terminal_replay=admit_terminal_replay,
    )
    if isinstance(reservation.task, PendingTaskRead | RunningTaskRead):
        await enqueuer.enqueue_document_transform(reservation.task.id)
    return TransformSubmission(task=reservation.task, replayed=not reservation.created)


def _validate_markdown_source(source: MarkdownSource) -> None:
    parse_markdown(source.content, title=source.name)
    validate_markdown_import(source.content, title=source.name)


async def retained_transform_output_available(task: TaskRead, files: DestinationFileReader) -> bool:
    if not isinstance(task, SucceededTaskRead) or not isinstance(
        task.result, DocumentTransformResult
    ):
        return False
    if task.result.output_strategy is OutputStrategy.IN_PLACE:
        return True
    try:
        await files.get(task.result.output.file_id)
    except FileNotInScopeError:
        return False
    except DriveUpstreamError as exc:
        if exc.http_status == status.HTTP_404_NOT_FOUND:
            return False
        raise
    return True


async def validate_transform_admission(
    payload: DocumentTransformRequest, files: DestinationFileReader
) -> None:
    source = await validate_transform_authorization(payload, files)
    validate_transform_freshness(payload, source)


async def validate_transform_authorization(
    payload: DocumentTransformRequest, files: DestinationFileReader
) -> FileResponse | None:
    if isinstance(payload.source, MarkdownSource):
        await _require_writable_destination(payload.source.destination_folder_id, files)
    if isinstance(payload.source, DriveFileSource):
        source = await files.get(payload.source.file_id)
        if payload.copy_destination_folder_id is not None:
            if source.mime_type != MIME_DOC:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="A copy destination is available only for native Google Docs",
                )
            await _require_writable_destination(payload.copy_destination_folder_id, files)
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
        return source
    return None


async def _require_writable_destination(file_id: FileId, files: DestinationFileReader) -> None:
    destination = await files.get(file_id)
    if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
        message = "Destination does not accept new files"
        raise FileNotInScopeError(message)


def validate_transform_freshness(
    payload: DocumentTransformRequest, source: FileResponse | None
) -> None:
    if isinstance(payload.source, DriveFileSource) and (
        source is None or source.modified_time != payload.source.expected_modified_time
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=SourceChangedError.public_detail,
            headers={"X-Wiki-Error-Code": SOURCE_CHANGED_ERROR_CODE},
        )
