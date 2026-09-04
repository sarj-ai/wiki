from __future__ import annotations

from time import monotonic
from typing import Annotated
import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status

from app.auth.dependencies import get_current_user
from app.config import settings
from app.document_transform.schemas import (
    DocumentTransformCapabilities,
    DocumentTransformRequest,
    OutputStrategy,
)
from app.identifiers import IdempotencyKey
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
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "In-place document transformation is disabled"
        }
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
    if isinstance(reservation.task, PendingTaskRead | RunningTaskRead):
        await enqueuer.enqueue_document_transform(reservation.task.id)
    response.headers["Idempotency-Replayed"] = str(not reservation.created).lower()
    response.headers["Retry-After"] = "1"
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return reservation.task
