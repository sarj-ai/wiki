from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.auth.dependencies import get_current_user
from app.identifiers import TaskId
from app.models.user import User
from app.openapi import authentication_responses
from app.tasks.dependencies import task_service_dep
from app.tasks.schemas import PendingTaskRead, RunningTaskRead, TaskRead
from app.tasks.service import TaskService


router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user)],
    responses=authentication_responses(),
)


@router.get(
    "/{task_id}",
    operation_id="get_task",
    responses={
        200: {
            "headers": {"Retry-After": {"schema": {"type": "integer"}}},
            "description": "Current task state.",
        }
    },
)
async def get_task(
    task_id: TaskId,
    service: Annotated[TaskService, Depends(task_service_dep)],
    user: Annotated[User, Depends(get_current_user)],
    response: Response,
) -> TaskRead:
    task = await service.get_for_owner(task_id, user.id)
    if isinstance(task, PendingTaskRead | RunningTaskRead):
        response.headers["Retry-After"] = "1"
    return task
