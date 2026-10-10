from __future__ import annotations

from functools import cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.tasks.queue import CloudTasksEnqueuer, TaskEnqueuer
from app.tasks.service import PostgresTaskService, TaskService


async def task_service_dep(db: Annotated[AsyncSession, Depends(get_db)]) -> TaskService:
    return PostgresTaskService(db=db)


async def task_enqueuer_dep() -> TaskEnqueuer:
    return _cached_task_enqueuer()


@cache
def _cached_task_enqueuer() -> TaskEnqueuer:
    project = _required(settings.GOOGLE_CLOUD_PROJECT, "GOOGLE_CLOUD_PROJECT")
    location = _required(settings.CLOUD_TASKS_LOCATION, "CLOUD_TASKS_LOCATION")
    queue = _required(settings.CLOUD_TASKS_QUEUE, "CLOUD_TASKS_QUEUE")
    target_url = _required(settings.CLOUD_TASKS_TARGET_URL, "CLOUD_TASKS_TARGET_URL")
    invoker = _required(
        settings.CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT,
        "CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT",
    )
    return CloudTasksEnqueuer(
        project=project,
        location=location,
        queue=queue,
        target_url=target_url,
        invoker_service_account=invoker,
        document_transform_queue=settings.DOCUMENT_TRANSFORM_QUEUE,
        document_transform_target_url=settings.DOCUMENT_TRANSFORM_TARGET_URL,
    )


def _required(value: str | None, name: str) -> str:
    if value is None:
        msg = f"{name} is required for Cloud Tasks"
        raise RuntimeError(msg)
    return value
