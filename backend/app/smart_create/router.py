from __future__ import annotations

import asyncio
from datetime import timedelta
from time import monotonic
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.files.dependencies import files_service_dep
from app.files.errors import DriveUpstreamError, FilesError
from app.files.service import FilesService
from app.identifiers import IdempotencyKey, TaskId
from app.models.user import User
from app.rate_limits.dependencies import rate_limit_service_dep
from app.rate_limits.service import (
    DatabaseRateLimitExceededError,
    RateLimitOperation,
    RateLimitPolicy,
    RateLimitService,
)
from app.sidebar.cache import SidebarCacheService
from app.sidebar.dependencies import sidebar_cache_service_dep
from app.smart_create.creation import SmartFileCreationService
from app.smart_create.dependencies import (
    smart_file_creation_service_dep,
    smart_proposal_service_dep,
)
from app.smart_create.errors import (
    SmartCreateTaskCancelledError,
    SmartCreateTaskFailedError,
    SmartCreateTaskInProgressError,
    SmartCreateTaskNotAdmittedError,
)
from app.smart_create.schemas import AIMode, SmartCreateResult, SmartFileCreate, SmartProposalRead
from app.smart_create.service import SmartProposalService
from app.tasks.dependencies import task_service_dep
from app.tasks.errors import TaskNotFoundError
from app.tasks.schemas import (
    CancelledTaskRead,
    FailedTaskRead,
    SucceededTaskRead,
    TaskKind,
    TaskRead,
)
from app.tasks.service import TaskService


router = APIRouter(prefix="/files", tags=["files"])

_SMART_CREATE_LEASE = timedelta(minutes=6)
_REPLAY_WAIT_SECONDS = 20.0
_REPLAY_POLL_SECONDS = 0.1


async def invalidate_smart_create_result(
    db: AsyncSession,
    sidebar_cache: SidebarCacheService,
    result: SmartCreateResult,
) -> None:
    if result.file.drive_id is not None:
        await sidebar_cache.invalidate_drive(db, result.file.drive_id)


@router.post("/smart-proposal")
async def create_smart_proposal(
    payload: AIMode,
    service: Annotated[SmartProposalService, Depends(smart_proposal_service_dep)],
    rate_limits: Annotated[RateLimitService, Depends(rate_limit_service_dep)],
    user: Annotated[User, Depends(get_current_user)],
    response: Response,
) -> SmartProposalRead:
    started = monotonic()
    await rate_limits.consume(
        user.id,
        RateLimitPolicy(
            operation=RateLimitOperation.SMART_PROPOSAL,
            limit=10,
            window=timedelta(minutes=1),
        ),
    )
    result = await service.propose(payload)
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
    return result


@router.post("/smart", status_code=status.HTTP_201_CREATED)
async def create_smart_file(
    payload: SmartFileCreate,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
    response: Response,
    user: Annotated[User, Depends(get_current_user)],
    tasks: Annotated[TaskService, Depends(task_service_dep)],
    rate_limits: Annotated[RateLimitService, Depends(rate_limit_service_dep)],
    creation: Annotated[SmartFileCreationService, Depends(smart_file_creation_service_dep)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    db: Annotated[AsyncSession, Depends(get_db)],
    sidebar_cache: Annotated[SidebarCacheService, Depends(sidebar_cache_service_dep)],
) -> SmartCreateResult:
    started = monotonic()
    reservation = await tasks.create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=user.id,
        idempotency_key=IdempotencyKey(idempotency_key),
        payload=payload,
    )
    task = reservation.task
    terminal = _terminal_result(task)
    if terminal is not None:
        result = terminal
        await invalidate_smart_create_result(db, sidebar_cache, result)
        _set_timing_headers(response, started=started, replayed=True)
        return result
    if not reservation.created:
        result = await _wait_for_replay(tasks, task.id, user.id)
        await invalidate_smart_create_result(db, sidebar_cache, result)
        _set_timing_headers(response, started=started, replayed=True)
        return result

    try:
        await rate_limits.consume(
            user.id,
            RateLimitPolicy(
                operation=RateLimitOperation.SMART_CREATE,
                limit=5,
                window=timedelta(minutes=1),
            ),
        )
    except DatabaseRateLimitExceededError:
        await tasks.discard_pending(task.id, owner_user_id=user.id)
        raise
    claim = await tasks.claim(task.id, lease_duration=_SMART_CREATE_LEASE)
    existing = await files.get(task.resource_id) if task.resource_id is not None else None
    try:
        result = await creation.execute(
            task_id=task.id,
            lease_token=claim.lease_token,
            payload=payload,
            existing_resource=existing,
        )
    except DriveUpstreamError:
        # Leave the lease in place. A retry after expiry reconciles by the
        # Drive appProperty before it is allowed to create anything again.
        raise
    except FilesError as exc:
        await tasks.fail(
            task.id,
            lease_token=claim.lease_token,
            error_code=exc.code,
            error_detail=str(exc),
        )
        raise
    await invalidate_smart_create_result(db, sidebar_cache, result)
    _set_timing_headers(response, started=started, replayed=False)
    return result


async def _wait_for_replay(
    tasks: TaskService,
    task_id: TaskId,
    owner_user_id: uuid.UUID,
) -> SmartCreateResult:
    deadline = monotonic() + _REPLAY_WAIT_SECONDS
    while True:
        try:
            task = await tasks.get_for_owner(task_id, owner_user_id)
        except TaskNotFoundError as exc:
            raise SmartCreateTaskNotAdmittedError from exc
        result = _terminal_result(task)
        if result is not None:
            return result
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise SmartCreateTaskInProgressError
        await asyncio.sleep(min(_REPLAY_POLL_SECONDS, remaining))


def _terminal_result(task: TaskRead) -> SmartCreateResult | None:
    if isinstance(task, SucceededTaskRead):
        return task.result
    if isinstance(task, FailedTaskRead):
        raise SmartCreateTaskFailedError(detail=task.error_detail)
    if isinstance(task, CancelledTaskRead):
        raise SmartCreateTaskCancelledError
    return None


def _set_timing_headers(response: Response, *, started: float, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    response.headers["Server-Timing"] = f"total;dur={(monotonic() - started) * 1000:.1f}"
