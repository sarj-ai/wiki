from __future__ import annotations

from time import monotonic
from typing import Annotated
import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Response, status

from app.auth.dependencies import get_current_user
from app.config import settings
from app.document_transform.application import (
    DestinationFileReader,
    get_current_document_polish,
    submit_document_transform,
)
from app.document_transform.dependencies import CurrentPolishContext, current_polish_context_dep
from app.document_transform.gcs_uploads import UploadUnavailableError, sign_markdown_upload
from app.document_transform.schemas import (
    CurrentDocumentPolish,
    DocumentTransformCapabilities,
    DocumentTransformRequest,
    MarkdownUpload,
    TransformMode,
)
from app.files.dependencies import files_service_dep
from app.identifiers import FileId, IdempotencyKey
from app.models.user import User
from app.openapi import authentication_responses
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import (
    DocumentTransformAdmission,
    TaskRead,
)
from app.tasks.service import TaskService


router = APIRouter(
    prefix="/document-transforms",
    tags=["document-transforms"],
    responses=authentication_responses(),
)


@router.post(
    "/uploads",
    status_code=status.HTTP_201_CREATED,
    operation_id="create_markdown_upload",
    summary="Get a signed GCS URL for direct Markdown upload",
    description=(
        "PUT UTF-8 Markdown bytes to url with exactly the returned headers, then submit "
        "source.kind=uploaded_markdown and upload_id to POST /document-transforms. "
        "The upload URL expires after ten minutes; uploads are private and limited to 512 KiB."
    ),
)
async def create_markdown_upload(
    user: Annotated[User, Depends(get_current_user)], response: Response
) -> MarkdownUpload:
    try:
        upload = await sign_markdown_upload(user.id)
    except UploadUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Direct Markdown upload is unavailable"
        ) from exc
    response.headers["Cache-Control"] = "no-store"
    return upload


@router.get(
    "/admissions/{idempotency_key}",
    status_code=status.HTTP_200_OK,
    operation_id="get_document_transform_admission",
    summary="Recover a transform submission by idempotency key",
    description=(
        "Use the same Idempotency-Key UUID after a timed-out or disconnected POST. "
        "A retained admission returns the task ID and status without resubmitting the transform."
    ),
    responses={404: {"description": "No retained admission for this owner and key"}},
)
async def get_document_transform_admission(
    idempotency_key: Annotated[uuid.UUID, Path()],
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
) -> DocumentTransformAdmission:
    response.headers["Cache-Control"] = "no-store"
    return await tasks.get_document_transform_admission(user.id, IdempotencyKey(idempotency_key))


@router.get(
    "/capabilities",
    operation_id="get_document_transform_capabilities",
    status_code=status.HTTP_200_OK,
)
async def get_document_transform_capabilities(
    _user: Annotated[User, Depends(get_current_user)],
) -> DocumentTransformCapabilities:
    return DocumentTransformCapabilities(in_place=settings.DOCUMENT_TRANSFORM_IN_PLACE_ENABLED)


@router.get(
    "/current",
    operation_id="get_current_document_polish",
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "description": "The source is not a native Google Doc",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The latest Google Docs revision could not be verified",
        },
    },
    status_code=status.HTTP_200_OK,
)
async def get_current_polish(
    file_id: Annotated[FileId, Query(min_length=1, max_length=128)],
    mode: Annotated[TransformMode, Query()],
    response: Response,
    context: Annotated[CurrentPolishContext, Depends(current_polish_context_dep)],
) -> CurrentDocumentPolish:
    response.headers["Cache-Control"] = "no-store"
    return await get_current_document_polish(
        file_id=file_id,
        mode=mode,
        owner_user_id=context.owner_user_id,
        tasks=context.tasks,
        files=context.files,
        revisions=context.revisions,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_document_transform",
    summary="Import Markdown or polish a Drive document",
    description=(
        "Submit a transform and poll the task in the Location header. Supply a new UUID "
        "in Idempotency-Key for each intended operation; reuse that UUID and the identical "
        "body for safe retries. After an ambiguous timeout, check "
        "GET /document-transforms/admissions/{idempotency_key} before resubmitting. "
        "For pasted Markdown, use source.kind=markdown with UTF-8 content. For a file, "
        "POST /document-transforms/uploads, PUT the bytes to the returned GCS URL, and "
        "submit source.kind=uploaded_markdown with upload_id, name, and destination_folder_id. "
        "Set mode to format, unslop, or both. Copy is the default."
    ),
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Accepted task, or the task retained for an identical retry.",
            "headers": {
                "Location": {"schema": {"type": "string"}},
                "Idempotency-Replayed": {"schema": {"type": "string", "enum": ["true", "false"]}},
                "Retry-After": {"schema": {"type": "integer"}},
            },
        },
        status.HTTP_409_CONFLICT: {
            "description": "The source changed after this transformation was requested",
            "headers": {
                "X-Wiki-Error-Code": {
                    "schema": {
                        "type": "string",
                        "enum": ["document_transform_source_changed"],
                    }
                }
            },
        },
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
    started = monotonic()
    submission = await submit_document_transform(
        owner_user_id=user.id,
        idempotency_key=IdempotencyKey(idempotency_key),
        payload=payload,
        tasks=tasks,
        enqueuer=enqueuer,
        files=files,
    )
    response.headers["Idempotency-Replayed"] = str(submission.replayed).lower()
    response.headers["Location"] = f"/tasks/{submission.task.id}"
    response.headers["Retry-After"] = str(submission.retry_after)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return submission.task
