from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.document_transform.dependencies import document_transform_task_executor_dep
from app.document_transform.task_executor import DocumentTransformTaskExecutor
from app.smart_create.dependencies import smart_create_task_executor_dep
from app.smart_create.executor import SmartCreateTaskExecutor
from app.tasks.internal_auth import (
    require_document_transform_cloud_task,
    require_smart_create_cloud_task,
)
from app.tasks.schemas import TaskDispatchRequest, TaskRead


router = APIRouter(
    prefix="/internal/tasks",
    tags=["internal-tasks"],
    include_in_schema=False,
)


@router.post(
    "/smart-create",
    dependencies=[Depends(require_smart_create_cloud_task)],
)
async def execute_smart_create_task(
    payload: TaskDispatchRequest,
    executor: Annotated[SmartCreateTaskExecutor, Depends(smart_create_task_executor_dep)],
) -> TaskRead:
    return await executor.execute(payload.task_id)


@router.post(
    "/document-transform",
    dependencies=[Depends(require_document_transform_cloud_task)],
)
async def execute_document_transform_task(
    payload: TaskDispatchRequest,
    executor: Annotated[
        DocumentTransformTaskExecutor,
        Depends(document_transform_task_executor_dep),
    ],
) -> TaskRead:
    return await executor.execute(payload.task_id)
