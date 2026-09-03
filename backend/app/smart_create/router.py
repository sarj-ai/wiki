from __future__ import annotations

from time import monotonic
from typing import Annotated
import uuid

from fastapi import APIRouter, Body, Depends, Header, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.identifiers import FileId, IdempotencyKey
from app.models.user import User
from app.openapi import authentication_responses
from app.sidebar.cache import SidebarCacheService
from app.smart_create.dependencies import reorganization_service_dep, smart_proposal_service_dep
from app.smart_create.schemas import (
    AIMode,
    ReorganizationProposalRead,
    SmartCreateResult,
    SmartFileCreate,
    SmartProposalRead,
)
from app.smart_create.service import ReorganizationService, SmartProposalService
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import PendingTaskRead, RunningTaskRead, TaskKind, TaskRead
from app.tasks.service import TaskService


router = APIRouter(prefix="/files", tags=["files"], responses=authentication_responses())


@router.post(
    "/{file_id}/reorganize-proposal",
    status_code=status.HTTP_200_OK,
    operation_id="propose_file_reorganization",
)
async def propose_file_reorganization(
    file_id: Annotated[FileId, Path(min_length=1)],
    service: Annotated[ReorganizationService, Depends(reorganization_service_dep)],
) -> ReorganizationProposalRead:
    return await service.propose(file_id)


async def invalidate_smart_create_result(
    db: AsyncSession,
    sidebar_cache: SidebarCacheService,
    result: SmartCreateResult,
) -> None:
    if result.file.drive_id is not None:
        await sidebar_cache.invalidate_drive(db, result.file.drive_id)


@router.post(
    "/smart-proposal",
    status_code=status.HTTP_200_OK,
    operation_id="create_smart_proposal",
    responses={
        200: {
            "headers": {"Server-Timing": {"schema": {"type": "string"}}},
            "description": "Smart creation proposal.",
        }
    },
)
async def create_smart_proposal(
    payload: Annotated[AIMode, Body()],
    service: Annotated[SmartProposalService, Depends(smart_proposal_service_dep)],
    _user: Annotated[User, Depends(get_current_user)],
    response: Response,
) -> SmartProposalRead:
    started = monotonic()
    result = await service.propose(payload)
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return result


@router.post(
    "/smart",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="create_smart_file",
    responses={
        202: {
            "headers": {
                "Idempotency-Replayed": {"schema": {"type": "boolean"}},
                "Retry-After": {"schema": {"type": "integer"}},
                "Server-Timing": {"schema": {"type": "string"}},
            },
            "description": "Accepted task or an existing idempotent task.",
        }
    },
)
async def create_smart_file(
    payload: Annotated[SmartFileCreate, Body()],
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    enqueuer: Annotated[TaskEnqueuer, Depends(task_enqueuer_dep)],
) -> TaskRead:
    started = monotonic()
    reservation = await tasks.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=user.id,
        idempotency_key=IdempotencyKey(idempotency_key),
        payload=payload,
    )
    if isinstance(reservation.task, PendingTaskRead | RunningTaskRead):
        await enqueuer.enqueue_smart_create(reservation.task.id)
    response.headers["Idempotency-Replayed"] = str(not reservation.created).lower()
    response.headers["Retry-After"] = "1"
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return reservation.task
