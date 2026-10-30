from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from app.document_transform.application import TransformSubmission
from app.identifiers import UserId
from app.mcp.context import McpAuthorizationError, ValidatedMcpPrincipal
from app.mcp.mutation import polish_document
from app.mcp.schemas import MarkdownImportInput, McpCapability, PolishInput
from app.tasks.schemas import PendingTaskRead, TaskKind


def request() -> PolishInput:
    return PolishInput.model_validate(
        {
            "file_id": "private-file",
            "mode": "format",
            "output_strategy": "copy",
            "expected_modified_time": "2026-09-05T12:00:00Z",
            "idempotency_key": str(uuid.uuid7()),
        }
    )


class Tasks:
    def __init__(self, task: PendingTaskRead | None = None) -> None:
        self.task = task
        self.owners: list[object] = []

    async def get_for_owner(self, task_id: object, owner: object) -> PendingTaskRead:
        self.owners.append((task_id, owner))
        assert self.task is not None
        return self.task


def context(capabilities: frozenset[McpCapability], tasks: Tasks | None = None) -> Any:
    return SimpleNamespace(
        principal=ValidatedMcpPrincipal(UserId(uuid.uuid7()), 1, capabilities),
        tasks=tasks or Tasks(),
        files=object(),
    )


@pytest.mark.parametrize(
    "capabilities",
    [frozenset(), frozenset({McpCapability.READ}), frozenset({McpCapability.TRANSFORM})],
    ids=["no-grant", "read-only", "transform-without-read"],
)
async def test_polish_denies_before_admission_or_provider_work(
    monkeypatch: pytest.MonkeyPatch, capabilities: frozenset[McpCapability]
):
    async def admission(**_kwargs: object) -> None:
        pytest.fail("Unauthorized mutation reached admission")

    monkeypatch.setattr("app.mcp.mutation.submit_document_transform", admission)
    current = context(capabilities)
    enqueuer: Any = object()
    with pytest.raises(McpAuthorizationError):
        await polish_document(request(), context=current, enqueuer=enqueuer)
    assert current.tasks.owners == []


@pytest.mark.parametrize("replayed", [False, True])
@pytest.mark.parametrize("source_kind", ["drive_file", "markdown"])
async def test_polish_preserves_shared_admission_and_authenticated_identity(
    monkeypatch: pytest.MonkeyPatch, replayed: bool, source_kind: str
):
    payload = (
        request()
        if source_kind == "drive_file"
        else MarkdownImportInput.model_validate(
            {
                "content": "# Report\n\nBody",
                "name": "Report",
                "destination_folder_id": "folder_123",
                "mode": "both",
                "idempotency_key": str(uuid.uuid7()),
            }
        )
    )
    enqueuer: Any = object()
    task = PendingTaskRead.model_validate(
        {
            "id": str(uuid.uuid7()),
            "kind": TaskKind.FILE_DOCUMENT_TRANSFORM,
            "status": "pending",
            "created_at": datetime.now(UTC),
            "attempt_count": 0,
            "result": None,
            "resource_id": None,
            "error_code": None,
            "error_detail": None,
            "started_at": None,
            "finished_at": None,
        }
    )
    current = context(frozenset(McpCapability), Tasks(task))
    calls: list[dict[str, object]] = []

    async def admission(**kwargs: object) -> TransformSubmission:
        calls.append(kwargs)
        return TransformSubmission(task=task, replayed=replayed)

    monkeypatch.setattr("app.mcp.mutation.submit_document_transform", admission)
    response = await polish_document(payload, context=current, enqueuer=enqueuer)
    assert calls == [
        {
            "payload": payload.to_transform_request(),
            "owner_user_id": current.principal.user_id,
            "idempotency_key": payload.idempotency_key,
            "tasks": current.tasks,
            "enqueuer": enqueuer,
            "files": current.files,
        }
    ]
    assert current.tasks.owners == [(task.id, current.principal.user_id)]
    assert response.task == task
    assert response.replayed is replayed
    assert response.retry_after_seconds == 1
