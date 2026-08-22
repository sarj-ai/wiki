from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.smart_create.dependencies import smart_create_task_executor_dep
from app.smart_create.executor import SmartCreateTaskExecutor
from app.tasks.internal_auth import require_cloud_task
from app.tasks.schemas import TaskDispatchRequest, TaskRead


router = APIRouter(
    prefix="/internal/tasks",
    tags=["internal-tasks"],
    dependencies=[Depends(require_cloud_task)],
    include_in_schema=False,
)


@router.post("/smart-create")
async def execute_smart_create_task(
    payload: TaskDispatchRequest,
    executor: Annotated[SmartCreateTaskExecutor, Depends(smart_create_task_executor_dep)],
) -> TaskRead:
    return await executor.execute(payload.task_id)
