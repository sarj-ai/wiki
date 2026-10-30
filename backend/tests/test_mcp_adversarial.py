from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, override
import uuid

from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
import pytest

from app.document_transform.application import TransformSubmission
from app.files.errors import FileNotInScopeError
from app.identifiers import TaskId, UserId
from app.mcp.context import ValidatedMcpPrincipal
from app.mcp.mutation import polish_document
from app.mcp.schemas import McpCapability, PolishInput
from app.mcp.server import create_mcp_server
from app.tasks.errors import TaskNotFoundError
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import SucceededTaskRead


class NoQueue(TaskEnqueuer):
    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        pytest.fail("Adversarial read reached the queue")


class Contexts:
    def __init__(self) -> None:
        self.user_id = uuid.uuid7()
        self.owners: list[uuid.UUID] = []
        self.opened = 0
        self.closed = asyncio.Event()
        self.entered = asyncio.Event()
        self.block = False

    @asynccontextmanager
    async def open(self) -> AsyncGenerator[Any]:
        self.opened += 1
        try:
            yield SimpleNamespace(
                principal=ValidatedMcpPrincipal(
                    UserId(self.user_id), 1, frozenset({McpCapability.READ})
                ),
                tasks=self,
            )
        finally:
            self.closed.set()

    async def get_for_owner(self, task_id: TaskId, owner: uuid.UUID) -> Any:
        self.owners.append(owner)
        self.entered.set()
        if self.block:
            await asyncio.Event().wait()
        raise TaskNotFoundError


def server(contexts: Contexts) -> Any:
    return create_mcp_server(auth=AuthProvider(), context_factory=contexts.open, enqueuer=NoQueue())


async def test_context_switch_does_not_reuse_previous_task_owner() -> None:
    contexts = Contexts()
    first, second = contexts.user_id, uuid.uuid7()
    arguments = {"request": {"task_id": str(uuid.uuid7())}}
    async with Client(server(contexts)) as client:
        for owner in (first, second, first):
            contexts.user_id = owner
            response = await client.call_tool("wiki_get_task", arguments, raise_on_error=False)
            assert response.is_error
    assert contexts.owners == [first, second, first]
    assert contexts.opened == 3


@pytest.mark.parametrize(
    "payload",
    [
        {"query": {"secret-canary": ["secret-canary"] * 2000}},
        {"query": "policy", "secret-canary": {"nested": "secret-canary"}},
        {"query": "policy", "limit": True},
        {"query": "policy", "capabilities": ["wiki:transform"]},
        {"query": "policy", "user_id": str(uuid.uuid7())},
    ],
    ids=["nested-large", "arbitrary-field-name", "boolean-limit", "scope", "user"],
)
async def test_nested_and_authority_injection_never_opens_context(
    payload: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    contexts = Contexts()
    async with Client(server(contexts)) as client:
        response = await client.call_tool("wiki_search", {"request": payload}, raise_on_error=False)
        assert response.is_error
        assert "secret-canary" not in str(response.content)
        assert "secret-canary" not in caplog.text
    assert contexts.opened == 0


async def test_deep_parsed_arguments_fail_with_constant_error() -> None:
    nested: Any = "secret-canary"
    for _ in range(1200):
        nested = [nested]
    contexts = Contexts()
    with pytest.raises(ToolError, match=r"^Invalid Wiki tool arguments$"):
        await server(contexts).call_tool("wiki_search", {"request": {"query": nested}})
    assert contexts.opened == 0


async def test_disconnect_closes_context_after_client_cancels_its_wait() -> None:
    contexts = Contexts()
    contexts.block = True
    async with Client(server(contexts)) as client:
        task = asyncio.create_task(
            client.call_tool("wiki_get_task", {"request": {"task_id": str(uuid.uuid7())}})
        )
        await asyncio.wait_for(contexts.entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.wait_for(contexts.closed.wait(), timeout=5)
    assert contexts.opened == 1


async def test_server_deadline_closes_abandoned_context_while_client_remains_connected(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("app.mcp.server._TOOL_TIMEOUT", timedelta(milliseconds=50))
    contexts = Contexts()
    contexts.block = True
    async with Client(server(contexts)) as client:
        task = asyncio.create_task(
            client.call_tool("wiki_get_task", {"request": {"task_id": str(uuid.uuid7())}})
        )
        await asyncio.wait_for(contexts.entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(contexts.closed.wait(), timeout=5)
        response = await client.call_tool("wiki_connection_status", {"request": {}})
        assert response.structured_content is not None
        assert response.structured_content["connected"] is True
        assert all(not record.exc_info for record in caplog.records)
    assert contexts.opened == 2


async def test_completed_replay_rechecks_output_acl_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    task = SucceededTaskRead.model_validate(
        {
            "id": str(uuid.uuid7()),
            "kind": "file.document_transform",
            "status": "succeeded",
            "created_at": now,
            "started_at": now,
            "finished_at": now,
            "attempt_count": 1,
            "resource_id": "private-output",
            "error_code": None,
            "error_detail": None,
            "result": {
                "operation_id": str(uuid.uuid7()),
                "source_sha256": "source",
                "output": {
                    "file_id": "private-output",
                    "name": "Private Output",
                    "parent_folder_id": "private-folder",
                },
                "mode": "format",
                "applied_patch_count": 0,
                "findings": [],
                "validation": {
                    "source_unchanged": True,
                    "protected_content_preserved": True,
                    "structure_preserved": True,
                    "unsafe_patch_count": 0,
                },
                "model_ids": [],
                "tool_versions": {},
                "pipeline_version": "test",
                "needs_review": True,
            },
        }
    )
    owner = uuid.uuid7()
    reads: list[str] = []

    class Services:
        async def get_for_owner(self, task_id: TaskId, user_id: uuid.UUID) -> SucceededTaskRead:
            assert task_id == task.id
            assert user_id == owner
            return task

        async def get(self, file_id: str) -> Any:
            reads.append(file_id)
            message = "Output access revoked"
            raise FileNotInScopeError(message)

    async def replay(**arguments: object) -> TransformSubmission:
        assert arguments["owner_user_id"] == owner
        return TransformSubmission(task=task, replayed=True)

    monkeypatch.setattr("app.mcp.mutation.submit_document_transform", replay)
    services = Services()
    context: Any = SimpleNamespace(
        principal=ValidatedMcpPrincipal(UserId(owner), 1, frozenset(McpCapability)),
        tasks=services,
        files=services,
    )
    payload = PolishInput.model_validate(
        {
            "file_id": "source",
            "mode": "format",
            "output_strategy": "copy",
            "expected_modified_time": "2026-09-05T00:00:00Z",
            "idempotency_key": str(uuid.uuid7()),
        }
    )
    with pytest.raises(FileNotInScopeError):
        await polish_document(payload, context=context, enqueuer=NoQueue())
    assert reads == ["private-output"]
