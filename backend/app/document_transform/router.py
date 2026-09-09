from __future__ import annotations

from time import monotonic
from typing import Annotated
import uuid

from fastapi import APIRouter, Body, Depends, Header, Path, Query, Response, status

from app.auth.dependencies import get_current_user
from app.config import settings
from app.document_transform.application import (
    DestinationFileReader,
    get_current_document_polish,
    submit_document_transform,
)
from app.document_transform.dependencies import CurrentPolishContext, current_polish_context_dep
from app.document_transform.schemas import (
    CurrentDocumentPolish,
    DocumentTransformCapabilities,
    DocumentTransformRequest,
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


@router.get(
    "/admissions/{idempotency_key}",
    status_code=status.HTTP_200_OK,
    operation_id="get_document_transform_admission",
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
    responses={
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
    response.headers["Retry-After"] = str(submission.retry_after)
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return submission.task
