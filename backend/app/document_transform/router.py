from __future__ import annotations

from time import monotonic
from typing import Annotated
import uuid

from fastapi import APIRouter, Body, Depends, Header, Path, Response, status

from app.auth.dependencies import get_current_user
from app.config import settings
from app.document_transform.application import DestinationFileReader, submit_document_transform
from app.document_transform.schemas import (
    DocumentTransformCapabilities,
    DocumentTransformRequest,
)
from app.files.dependencies import files_service_dep
from app.identifiers import IdempotencyKey
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
