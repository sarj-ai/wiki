from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal, assert_never, override

from google.api_core.exceptions import AlreadyExists, GoogleAPICallError
from google.cloud import tasks_v2

from app.identifiers import TaskId
from app.runtime_config import (
    ConfiguredCloudTasks,
    ConfiguredDocumentQueue,
    DisabledDocumentQueue,
    DocumentQueueConfiguration,
)
from app.tasks.errors import TaskEnqueueError
from app.tasks.schemas import TaskDispatchRequest


class TaskEnqueuer(ABC):
    @abstractmethod
    async def enqueue_smart_create(self, task_id: TaskId) -> None: ...

    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class _DisabledDocumentTransformQueue:
    kind: ClassVar[Literal["disabled"]] = "disabled"


@dataclass(frozen=True, slots=True)
class _ConfiguredDocumentTransformQueue:
    kind: ClassVar[Literal["configured"]] = "configured"
    parent: str
    request_template: bytes


type _DocumentTransformQueue = _DisabledDocumentTransformQueue | _ConfiguredDocumentTransformQueue


class CloudTasksEnqueuer(TaskEnqueuer):
    def __init__(
        self,
        *,
        configuration: ConfiguredCloudTasks,
        document_transform: DocumentQueueConfiguration,
        client: tasks_v2.CloudTasksAsyncClient | None = None,
    ) -> None:
        self._client = client or tasks_v2.CloudTasksAsyncClient()
        self._parent = self._client.queue_path(
            configuration.project, configuration.location, configuration.queue
        )
        self._smart_create_request = _http_request_template(
            configuration.target_url, configuration.invoker_service_account
        )
        self._document_transform: _DocumentTransformQueue
        match document_transform:
            case ConfiguredDocumentQueue():
                self._document_transform = _ConfiguredDocumentTransformQueue(
                    self._client.queue_path(
                        configuration.project, configuration.location, document_transform.queue
                    ),
                    _http_request_template(
                        document_transform.target_url, configuration.invoker_service_account
                    ),
                )
            case DisabledDocumentQueue():
                self._document_transform = _DisabledDocumentTransformQueue()
            case _:
                assert_never(document_transform)

    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        await self._enqueue(
            task_id,
            parent=self._parent,
            prefix="smart-create",
            request_template=self._smart_create_request,
        )

    @override
    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        match self._document_transform:
            case _ConfiguredDocumentTransformQueue(parent, request_template):
                await self._enqueue(
                    task_id,
                    parent=parent,
                    prefix="document-transform",
                    request_template=request_template,
                )
            case _DisabledDocumentTransformQueue():
                msg = "Document Transform Cloud Tasks queue is not configured"
                raise RuntimeError(msg)
            case _ as unexpected:
                assert_never(unexpected)

    async def _enqueue(
        self,
        task_id: TaskId,
        *,
        parent: str,
        prefix: str,
        request_template: bytes,
    ) -> None:
        request = TaskDispatchRequest(task_id=task_id)
        # The template contains only immutable queue configuration. Each
        # enqueue owns its protobuf, body, headers, and deterministic task name.
        http_request = tasks_v2.HttpRequest.deserialize(request_template)
        http_request.body = request.model_dump_json().encode()
        task = tasks_v2.Task(
            name=f"{parent}/tasks/{prefix}-{task_id.hex}",
            http_request=http_request,
        )
        try:
            await self._client.create_task(parent=parent, task=task)
        except AlreadyExists:
            return
        except GoogleAPICallError as exc:
            raise TaskEnqueueError from exc


def _http_request_template(target_url: str, service_account: str) -> bytes:
    return bytes(
        tasks_v2.HttpRequest.serialize(
            tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=target_url,
                headers={"Content-Type": "application/json"},
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=service_account, audience=target_url
                ),
            )
        )
    )
