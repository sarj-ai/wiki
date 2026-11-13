from __future__ import annotations

import asyncio
import json
from typing import override
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


@pytest.mark.parametrize(
    ("document_queue", "document_target"),
    [
        ("document-transform", None),
        (None, "https://wiki-api.sarj.ai/internal/tasks/document-transform"),
    ],
)
def test_partial_transform_configuration_fails_before_client_use(
    document_queue: str | None,
    document_target: str | None,
) -> None:
    class UnusedClient(_Client):
        @override
        def queue_path(self, project: str, location: str, queue: str) -> str:
            msg = "Invalid configuration must not use the provider client"
            raise AssertionError(msg)

    with pytest.raises(ValueError, match="configured together"):
        CloudTasksEnqueuer(
            project="sarj-docs",
            location="us-central1",
            queue="smart-create",
            target_url="https://wiki-api.sarj.ai/internal/tasks/smart-create",
            invoker_service_account="wiki-task-invoker@sarj-docs.iam.gserviceaccount.com",
            document_transform_queue=document_queue,
            document_transform_target_url=document_target,
            client=dependency(UnusedClient()),
        )


async def test_disabled_transform_queue_refuses_dispatch_without_affecting_smart_create() -> None:
    client = _Client()
    enqueuer = CloudTasksEnqueuer(
        project="sarj-docs",
        location="us-central1",
        queue="smart-create",
        target_url="https://wiki-api.sarj.ai/internal/tasks/smart-create",
        invoker_service_account="wiki-task-invoker@sarj-docs.iam.gserviceaccount.com",
        client=dependency(client),
    )
    task_id = TaskId(uuid.uuid7())
    with pytest.raises(RuntimeError, match="not configured"):
        await enqueuer.enqueue_document_transform(task_id)
    assert client.task is None
    await enqueuer.enqueue_smart_create(task_id)
    assert client.task is not None
    assert client.task.name.endswith(f"/tasks/smart-create-{task_id.hex}")


async def test_concurrent_dispatches_keep_their_queue_payload_and_oidc_target() -> None:
    class RecordingClient(_Client):
        def __init__(self) -> None:
            super().__init__()
            self.tasks: list[tasks_v2.Task] = []

        @override
        async def create_task(self, *, parent: str, task: tasks_v2.Task) -> tasks_v2.Task:
            self.tasks.append(task)
            await asyncio.sleep(0)
            return task

    client = RecordingClient()
    enqueuer = _enqueuer(client)
    smart_id = TaskId(uuid.uuid7())
    transform_id = TaskId(uuid.uuid7())
    await asyncio.gather(
        enqueuer.enqueue_smart_create(smart_id),
        enqueuer.enqueue_document_transform(transform_id),
    )
    assert len(client.tasks) == 2
    assert client.tasks[0] is not client.tasks[1]
    for task, expected_id, path in zip(
        client.tasks,
        (smart_id, transform_id),
        ("smart-create", "document-transform"),
        strict=True,
    ):
        assert task.name.endswith(f"/tasks/{path}-{expected_id.hex}")
        assert json.loads(task.http_request.body) == {"task_id": str(expected_id)}
        assert task.http_request.url == f"https://wiki-api.sarj.ai/internal/tasks/{path}"
        assert task.http_request.oidc_token.audience == task.http_request.url
