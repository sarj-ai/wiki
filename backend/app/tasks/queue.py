from __future__ import annotations

from abc import ABC, abstractmethod
from typing import override
from urllib.parse import urlsplit

from google.api_core.exceptions import AlreadyExists, GoogleAPICallError
from google.cloud import tasks_v2

from app.identifiers import TaskId
from app.tasks.errors import TaskEnqueueError
from app.tasks.schemas import TaskDispatchRequest


class TaskEnqueuer(ABC):
    @abstractmethod
    async def enqueue_smart_create(self, task_id: TaskId) -> None: ...

    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        raise NotImplementedError


class CloudTasksEnqueuer(TaskEnqueuer):
    def __init__(
        self,
        *,
        project: str,
        location: str,
        queue: str,
        target_url: str,
        invoker_service_account: str,
        document_transform_queue: str | None = None,
        document_transform_target_url: str | None = None,
        client: tasks_v2.CloudTasksAsyncClient | None = None,
    ) -> None:
        if not _is_internal_task_url(target_url, expected_path="/internal/tasks/smart-create"):
            msg = "Cloud Tasks target must be the HTTPS internal Smart Create endpoint"
            raise ValueError(msg)
        self._client = client or tasks_v2.CloudTasksAsyncClient()
        self._parent = self._client.queue_path(project, location, queue)
        self._document_transform_parent = (
            self._client.queue_path(project, location, document_transform_queue)
            if document_transform_queue is not None
            else None
        )
        if document_transform_target_url is not None and not _is_internal_task_url(
            document_transform_target_url, expected_path="/internal/tasks/document-transform"
        ):
            msg = "Cloud Tasks target must be the HTTPS internal Document Transform endpoint"
            raise ValueError(msg)
        self._smart_create_request = _http_request_template(target_url, invoker_service_account)
        self._document_transform_request = (
            _http_request_template(document_transform_target_url, invoker_service_account)
            if document_transform_target_url is not None
            else None
        )

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
        if self._document_transform_parent is None or self._document_transform_request is None:
            msg = "Document Transform Cloud Tasks queue is not configured"
            raise RuntimeError(msg)
        await self._enqueue(
            task_id,
            parent=self._document_transform_parent,
            prefix="document-transform",
            request_template=self._document_transform_request,
        )

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


def _is_internal_task_url(target_url: str, *, expected_path: str) -> bool:
    parsed = urlsplit(target_url)
    has_authority = (
        parsed.hostname is not None and parsed.username is None and parsed.password is None
    )
    has_expected_location = (
        parsed.scheme == "https"
        and parsed.path == expected_path
        and not parsed.query
        and not parsed.fragment
    )
    return has_authority and has_expected_location
