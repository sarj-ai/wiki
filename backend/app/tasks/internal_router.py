from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends

from app.document_transform.dependencies import document_transform_task_executor_dep
from app.document_transform.task_executor import DocumentTransformTaskExecutor
from app.performance import provider_retry_budget
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

_DOCUMENT_TRANSFORM_PROVIDER_BUDGET = timedelta(minutes=4)


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
    # Interactive requests keep a strict 20-second aggregate provider budget. This
    # authenticated Cloud Tasks route runs behind a 300-second Cloud Run timeout and
    # needs enough room for the model pass plus idempotent Google Docs mutations.
    with provider_retry_budget(_DOCUMENT_TRANSFORM_PROVIDER_BUDGET):
        return await executor.execute(payload.task_id)
