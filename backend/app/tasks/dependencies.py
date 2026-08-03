from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.tasks.service import PostgresTaskService, TaskService


def task_service_dep(db: Annotated[AsyncSession, Depends(get_db)]) -> TaskService:
    return PostgresTaskService(db=db)
