from __future__ import annotations

from functools import cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.runtime_config import require_cloud_tasks
from app.tasks.queue import CloudTasksEnqueuer, TaskEnqueuer
from app.tasks.service import PostgresTaskService, TaskService


async def task_service_dep(db: Annotated[AsyncSession, Depends(get_db)]) -> TaskService:
    return PostgresTaskService(db=db)


async def task_enqueuer_dep() -> TaskEnqueuer:
    return _cached_task_enqueuer()


@cache
def _cached_task_enqueuer() -> TaskEnqueuer:
    return CloudTasksEnqueuer(
        configuration=require_cloud_tasks(settings.cloud_tasks),
        document_transform=settings.DOCUMENT.queue,
    )
