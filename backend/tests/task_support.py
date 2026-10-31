from abc import ABC
from datetime import timedelta
from typing import override
import uuid

from pydantic import BaseModel

from app.identifiers import FileId, TaskId
from app.tasks.schemas import (
    DocumentTransformTaskRecord,
    SmartCreateTaskRecord,
    TaskClaim,
    TaskRead,
)
from app.tasks.service import TaskService


class StrictTaskService(TaskService, ABC):
    @override
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        raise NotImplementedError

    @override
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead:
        raise NotImplementedError

    @override
    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord:
        raise NotImplementedError

    @override
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        raise NotImplementedError

    @override
    async def record_resource(
        self, task_id: TaskId, *, lease_token: uuid.UUID, resource_id: FileId
    ) -> None:
        raise NotImplementedError

    @override
    async def succeed(
        self, task_id: TaskId, *, lease_token: uuid.UUID, result: BaseModel
    ) -> TaskRead:
        raise NotImplementedError
