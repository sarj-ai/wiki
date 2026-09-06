from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, override
import uuid

from fastmcp import Client
from fastmcp.server.auth import AuthProvider
import pytest

from app.identifiers import TaskId
from app.mcp.context import ValidatedMcpPrincipal
from app.mcp.schemas import McpCapability
from app.mcp.server import create_mcp_server
from app.tasks.queue import TaskEnqueuer


class Enqueuer(TaskEnqueuer):
    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        pytest.fail("Unexpected queue submission")


class Contexts:
    def __init__(self) -> None:
        self.opened = 0
        self.closed = 0
        self.capabilities = frozenset({McpCapability.READ})
        self.error = False

    @asynccontextmanager
    async def open(self) -> AsyncGenerator[Any]:
        self.opened += 1
        try:
            if self.error:
                message = "https://provider.invalid?access_token=secret-canary"
                raise RuntimeError(message)
            yield SimpleNamespace(
                principal=ValidatedMcpPrincipal(uuid.uuid7(), 1, self.capabilities)
            )
        finally:
            self.closed += 1


def server(contexts: Contexts) -> Any:
    return create_mcp_server(
        auth=AuthProvider(), context_factory=contexts.open, enqueuer=Enqueuer()
    )


async def test_exact_curated_tools_and_annotations() -> None:
    async with Client(server(Contexts())) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {
            "wiki_connection_status",
            "wiki_search",
            "wiki_get_document",
            "wiki_polish_document",
            "wiki_get_polish_admission",
            "wiki_get_task",
            "wiki_get_short_link",
        }
        assert await client.list_resources() == []
        assert await client.list_prompts() == []
        for tool in tools:
            assert set(tool.inputSchema["properties"]) == {"request"}
            assert tool.annotations is not None
            writes = tool.name == "wiki_polish_document"
            assert tool.annotations.readOnlyHint is not writes
            assert tool.annotations.destructiveHint is writes


async def test_each_call_opens_fresh_context_and_rechecks_authorization() -> None:
    contexts = Contexts()
    async with Client(server(contexts)) as client:
        for _ in range(2):
            result = await client.call_tool("wiki_connection_status", {"request": {}})
            assert result.structured_content is not None
            assert result.structured_content["capabilities"] == ["wiki:read"]
        contexts.capabilities = frozenset()
        result = await client.call_tool(
            "wiki_connection_status", {"request": {}}, raise_on_error=False
        )
        assert result.is_error
        assert "authorization required" in str(result.content)
    assert contexts.opened == contexts.closed == 3


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"request": {}, "principal": "secret-canary"},
        {"request": {"query": "secret-canary", "owner": "other-user"}},
        {"request": {"query": "secret-canary", "limit": "20"}},
        {"request": {"query": "secret-canary" * 2000}},
    ],
    ids=["missing", "principal-injection", "owner-injection", "coerced-limit", "oversized"],
)
async def test_invalid_arguments_are_bounded_and_not_logged(
    arguments: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    contexts = Contexts()
    async with Client(server(contexts)) as client:
        result = await client.call_tool("wiki_search", arguments, raise_on_error=False)
        assert result.is_error
        assert "secret-canary" not in str(result.content)
        assert "secret-canary" not in caplog.text
    assert contexts.opened == 0


async def test_provider_error_is_sanitized_without_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    contexts = Contexts()
    contexts.error = True
    async with Client(server(contexts)) as client:
        result = await client.call_tool(
            "wiki_connection_status", {"request": {}}, raise_on_error=False
        )
        assert result.is_error
        assert "secret-canary" not in str(result.content)
        assert "secret-canary" not in caplog.text
        assert all(not record.exc_info for record in caplog.records)
    assert contexts.closed == 1


def test_auth_provider_is_mandatory() -> None:
    invalid_auth: Any = None
    with pytest.raises(TypeError, match="explicit MCP authentication"):
        create_mcp_server(auth=invalid_auth, context_factory=Contexts().open, enqueuer=Enqueuer())


async def test_read_grant_cannot_dispatch_polish(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Mutation reached without explicit transform capability")

    monkeypatch.setattr("app.mcp.server.polish_document", forbidden)
    contexts = Contexts()
    async with Client(server(contexts)) as client:
        result = await client.call_tool(
            "wiki_polish_document",
            {
                "request": {
                    "file_id": "private-file",
                    "expected_modified_time": "2026-09-05T00:00:00Z",
                    "mode": "format",
                    "output_strategy": "copy",
                    "idempotency_key": str(uuid.uuid7()),
                }
            },
            raise_on_error=False,
        )
        assert result.is_error
        assert "authorization required" in str(result.content)
    assert contexts.opened == contexts.closed == 1
