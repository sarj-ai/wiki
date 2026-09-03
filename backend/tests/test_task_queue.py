from __future__ import annotations

import asyncio
import json
import uuid

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
import pytest

from app.identifiers import TaskId
from app.tasks import dependencies
from app.tasks.queue import CloudTasksEnqueuer, TaskEnqueuer
from tests.support import dependency


class _Client:
    def __init__(self, *, duplicate: bool = False) -> None:
        self.duplicate = duplicate
        self.task: tasks_v2.Task | None = None

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task:
        if self.duplicate:
            msg = "deterministic task already exists"
            raise AlreadyExists(msg)
        self.task = task
        return task


def _enqueuer(client: _Client) -> CloudTasksEnqueuer:
    return CloudTasksEnqueuer(
        project="sarj-docs",
        location="us-central1",
        queue="smart-create",
        target_url="https://wiki-api.sarj.ai/internal/tasks/smart-create",
        invoker_service_account="wiki-task-invoker@sarj-docs.iam.gserviceaccount.com",
        document_transform_queue="document-transform",
        document_transform_target_url=(
            "https://wiki-api.sarj.ai/internal/tasks/document-transform"
        ),
        client=dependency(client),
    )


async def test_enqueue_uses_deterministic_name_oidc_and_id_only_body() -> None:
    client = _Client()
    task_id = TaskId(uuid.uuid7())

    await _enqueuer(client).enqueue_smart_create(task_id)

    assert client.task is not None
    assert client.task.name.endswith(f"/tasks/smart-create-{task_id.hex}")
    request = client.task.http_request
    assert request.http_method == tasks_v2.HttpMethod.POST
    assert request.oidc_token.audience == request.url
    assert json.loads(request.body) == {"task_id": str(task_id)}


async def test_duplicate_cloud_task_name_is_idempotent_success() -> None:
    result = await _enqueuer(_Client(duplicate=True)).enqueue_smart_create(TaskId(uuid.uuid7()))

    assert result is None


async def test_document_transform_uses_separate_queue_and_exact_internal_target() -> None:
    client = _Client()
    task_id = TaskId(uuid.uuid7())

    await _enqueuer(client).enqueue_document_transform(task_id)

    assert client.task is not None
    assert "/queues/document-transform/tasks/document-transform-" in client.task.name
    assert client.task.http_request.url.endswith("/internal/tasks/document-transform")
    assert client.task.http_request.oidc_token.audience == client.task.http_request.url


@pytest.mark.parametrize(
    "target_url",
    [
        "http://wiki-api.sarj.ai/internal/tasks/smart-create",
        "https://wiki-api.sarj.ai/internal/tasks/other",
        "https://user@wiki-api.sarj.ai/internal/tasks/smart-create",
        "https://wiki-api.sarj.ai/internal/tasks/smart-create?redirect=https://attacker.example",
    ],
)
def test_rejects_unsafe_or_unexpected_task_targets(target_url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS internal Smart Create endpoint"):
        CloudTasksEnqueuer(
            project="sarj-docs",
            location="us-central1",
            queue="smart-create",
            target_url=target_url,
            invoker_service_account="wiki-task-invoker@sarj-docs.iam.gserviceaccount.com",
            client=dependency(_Client()),
        )


async def test_dependency_constructs_async_client_on_request_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExpectedEnqueuer(TaskEnqueuer):
        async def enqueue_smart_create(self, task_id: TaskId) -> None:
            del task_id

    expected = _ExpectedEnqueuer()

    def construct() -> TaskEnqueuer:
        asyncio.get_running_loop()
        return expected

    monkeypatch.setattr(dependencies, "_cached_task_enqueuer", construct)

    assert await dependencies.task_enqueuer_dep() is expected
