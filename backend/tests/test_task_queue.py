from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from typing import cast
import uuid

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
import pytest

from app.identifiers import TaskId
from app.tasks import dependencies
from app.tasks.queue import CloudTasksEnqueuer, TaskEnqueuer


class _Client:
    def __init__(self, *, duplicate: bool = False) -> None:
        self.duplicate = duplicate
        self.task: tasks_v2.Task | None = None

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task:
        if self.duplicate:
            factory = cast("Callable[[str], AlreadyExists]", AlreadyExists)
            msg = "deterministic task already exists"
            raise factory(msg)
        self.task = task
        return task


def _enqueuer(client: _Client) -> CloudTasksEnqueuer:
    return CloudTasksEnqueuer(
        project="sarj-docs",
        location="us-central1",
        queue="smart-create",
        target_url="https://wiki-api.sarj.ai/internal/tasks/smart-create",
        invoker_service_account="wiki-task-invoker@sarj-docs.iam.gserviceaccount.com",
        client=cast("tasks_v2.CloudTasksAsyncClient", client),
    )


async def test_enqueue_uses_deterministic_name_oidc_and_id_only_body() -> None:
    client = _Client()
    task_id = TaskId(uuid.uuid4())

    await _enqueuer(client).enqueue_smart_create(task_id)

    assert client.task is not None
    assert client.task.name.endswith(f"/tasks/smart-create-{task_id.hex}")
    request = client.task.http_request
    assert request.http_method == tasks_v2.HttpMethod.POST
    assert request.oidc_token.audience == request.url
    assert json.loads(request.body) == {"task_id": str(task_id)}


async def test_duplicate_cloud_task_name_is_idempotent_success() -> None:
    result = await _enqueuer(_Client(duplicate=True)).enqueue_smart_create(TaskId(uuid.uuid4()))

    assert result is None


async def test_dependency_constructs_async_client_on_request_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = cast("TaskEnqueuer", object())

    def construct() -> TaskEnqueuer:
        asyncio.get_running_loop()
        return expected

    monkeypatch.setattr(dependencies, "_cached_task_enqueuer", construct)

    assert await dependencies.task_enqueuer_dep() is expected
