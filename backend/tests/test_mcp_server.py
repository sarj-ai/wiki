from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, override
import uuid

from fastmcp import Client
from fastmcp.server.auth import AuthProvider
import pytest

from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.identifiers import TaskId, UserId
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
                principal=ValidatedMcpPrincipal(UserId(uuid.uuid7()), 1, self.capabilities)
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
            "wiki_import_markdown",
            "wiki_get_polish_admission",
            "wiki_get_task",
            "wiki_get_short_link",
        }
        assert await client.list_resources() == []
        assert await client.list_prompts() == []
        for tool in tools:
            assert set(tool.input_schema["properties"]) == {"request"}
            assert tool.annotations is not None
            writes = tool.name in {"wiki_polish_document", "wiki_import_markdown"}
            assert tool.annotations.read_only_hint is not writes
            assert tool.annotations.destructive_hint is writes
        import_tool = next(tool for tool in tools if tool.name == "wiki_import_markdown")
        assert "read its UTF-8 contents" in (import_tool.description or "")
        assert "poll wiki_get_task" in (import_tool.description or "")


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


async def test_markdown_import_accepts_document_sized_input_then_checks_transform_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Mutation reached without explicit transform capability")

    monkeypatch.setattr("app.mcp.server.polish_document", forbidden)
    contexts = Contexts()
    async with Client(server(contexts)) as client:
        result = await client.call_tool(
            "wiki_import_markdown",
            {
                "request": {
                    "content": "# Title\n\n" + "paragraph\n" * 2000,
                    "name": "Report",
                    "destination_folder_id": "folder_123",
                    "mode": "both",
                    "idempotency_key": str(uuid.uuid7()),
                }
            },
            raise_on_error=False,
        )
        assert result.is_error
        assert "authorization required" in str(result.content)
    assert contexts.opened == contexts.closed == 1


async def test_markdown_import_accepts_exact_limit_even_with_json_escaped_controls() -> None:
    contexts = Contexts()
    async with Client(server(contexts)) as client:
        result = await client.call_tool(
            "wiki_import_markdown",
            {
                "request": {
                    "content": "\x01" * MAX_MARKDOWN_BYTES,
                    "name": "Report",
                    "destination_folder_id": "folder_123",
                    "mode": "format",
                    "idempotency_key": str(uuid.uuid7()),
                }
            },
            raise_on_error=False,
        )
        assert result.is_error
        assert "authorization required" in str(result.content)
    assert contexts.opened == contexts.closed == 1


async def test_markdown_import_rejects_content_above_limit_before_context() -> None:
    contexts = Contexts()
    async with Client(server(contexts)) as client:
        result = await client.call_tool(
            "wiki_import_markdown",
            {
                "request": {
                    "content": "\x01" * (MAX_MARKDOWN_BYTES + 1),
                    "name": "Report",
                    "destination_folder_id": "folder_123",
                    "mode": "format",
                    "idempotency_key": str(uuid.uuid7()),
                }
            },
            raise_on_error=False,
        )
        assert result.is_error
        assert "Invalid Wiki tool arguments" in str(result.content)
    assert contexts.opened == 0
