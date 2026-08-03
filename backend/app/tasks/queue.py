from __future__ import annotations

from abc import ABC, abstractmethod
from typing import override

from google.api_core.exceptions import AlreadyExists, GoogleAPICallError
from google.cloud import tasks_v2

from app.identifiers import TaskId
from app.tasks.errors import TaskEnqueueError
from app.tasks.schemas import TaskDispatchRequest


class TaskEnqueuer(ABC):
    @abstractmethod
    async def enqueue_smart_create(self, task_id: TaskId) -> None: ...


class CloudTasksEnqueuer(TaskEnqueuer):
    def __init__(
        self,
        *,
        project: str,
        location: str,
        queue: str,
        target_url: str,
        invoker_service_account: str,
        client: tasks_v2.CloudTasksAsyncClient | None = None,
    ) -> None:
        self._client = client or tasks_v2.CloudTasksAsyncClient()
        self._parent = self._client.queue_path(project, location, queue)
        self._target_url = target_url
        self._invoker_service_account = invoker_service_account

    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        request = TaskDispatchRequest(task_id=task_id)
        task = tasks_v2.Task(
            name=f"{self._parent}/tasks/smart-create-{task_id.hex}",
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=self._target_url,
                headers={"Content-Type": "application/json"},
                body=request.model_dump_json().encode(),
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self._invoker_service_account,
                    audience=self._target_url,
                ),
            ),
        )
        try:
            await self._client.create_task(parent=self._parent, task=task)
        except AlreadyExists:
            return
        except GoogleAPICallError as exc:
            raise TaskEnqueueError from exc
