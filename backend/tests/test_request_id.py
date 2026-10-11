from __future__ import annotations

from dataclasses import dataclass

import loguru
from loguru import logger
import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from app.performance import add_duration, increment
from app.request_id import RequestIDMiddleware


@dataclass(frozen=True, slots=True)
class _RunResult:
    messages: list[Message]
    records: list[loguru.Record]


async def _receive() -> Message:
    return {"body": b"", "more_body": False, "type": "http.request"}


def _scope(request_id: bytes | None = None, *, path: str = "/health") -> Scope:
    headers = [] if request_id is None else [(b"x-request-id", request_id)]
    scope: Scope = {
        "headers": headers,
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "query_string": b"secret=not-logged",
        "scheme": "https",
        "server": ("api.example.test", 443),
        "type": "http",
    }
    return scope


async def _run(
    request_id: bytes | None = None,
    *,
    path: str = "/health",
    route_template: str | None = "/health",
    static_routes: frozenset[str] = frozenset(),
) -> _RunResult:
    messages: list[Message] = []
    records: list[loguru.Record] = []

    async def endpoint(_request: Request) -> Response:
        return Response()

    async def app(scope: Scope, _receive: Receive, send: Send) -> None:
        if route_template is not None:
            scope["route"] = Route(route_template, endpoint)
        add_duration("auth", 2.5)
        increment("db_query_count")
        await send({"headers": [], "status": 204, "type": "http.response.start"})
        await send({"body": b"", "type": "http.response.body"})

    async def send(message: Message) -> None:
        messages.append(message)

    sink_id = logger.add(
        lambda message: records.append(message.record),
        filter=lambda record: record["message"] == "request.complete",
    )
    try:
        await RequestIDMiddleware(app, static_routes=static_routes)(
            _scope(request_id, path=path), _receive, send
        )
    finally:
        logger.remove(sink_id)
    return _RunResult(messages=messages, records=records)


def _response_request_id(messages: list[Message]) -> str:
    start = next(message for message in messages if message["type"] == "http.response.start")
    headers = dict(start["headers"])
    return headers[b"x-request-id"].decode()


@pytest.mark.asyncio
async def test_generates_request_id_and_logs_safe_completion_fields() -> None:
    result = await _run()

    request_id = _response_request_id(result.messages)
    assert len(request_id) == 12
    assert request_id.isalnum()
    assert len(result.records) == 1
    assert result.records[0]["extra"]["request_method"] == "GET"
    assert result.records[0]["extra"]["request_route"] == "/health"
    assert result.records[0]["extra"]["status_code"] == 204
    assert result.records[0]["extra"]["auth_ms"] == 2.5
    assert result.records[0]["extra"]["db_query_count"] == 1
    assert result.records[0]["extra"]["duration_ms"] >= 0
    assert "secret" not in str(result.records[0]["extra"])
    start = next(message for message in result.messages if message["type"] == "http.response.start")
    timing = dict(start["headers"])[b"server-timing"]
    assert timing.startswith(b"app;dur=")
    assert b"auth;dur=2.5" in timing
    assert result.records[0]["extra"]["response_bytes"] == 0


@pytest.mark.asyncio
async def test_logs_route_template_without_concrete_resource_identifier() -> None:
    resource_id = "sensitive-google-drive-file-id"
    result = await _run(
        path=f"/files/{resource_id}",
        route_template="/files/{file_id}",
    )
    assert result.records[0]["extra"]["request_route"] == "/files/{file_id}"
    assert resource_id not in str(result.records[0])


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/token", "/token"), ("/token/private-id", "unmatched"), ("/files/private-id", "unmatched")],
)
async def test_only_logs_whitelisted_unmatched_static_routes(path: str, expected: str) -> None:
    result = await _run(path=path, route_template=None, static_routes=frozenset({"/token"}))
    assert result.records[0]["extra"]["request_route"] == expected
    assert "private-id" not in str(result.records[0])
    assert "secret" not in str(result.records[0]["extra"])


@pytest.mark.asyncio
async def test_preserves_valid_request_id_exactly() -> None:
    result = await _run(b"wiki-abcdef012345-2")
    assert _response_request_id(result.messages) == "wiki-abcdef012345-2"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [b" leading-space", b"line\nbreak", b"control\x00byte", b"a" * 65],
)
async def test_replaces_invalid_request_id(invalid: bytes) -> None:
    result = await _run(invalid)
    generated = _response_request_id(result.messages)
    assert generated != invalid.decode("latin-1")
    assert len(generated) == 12


@pytest.mark.asyncio
async def test_logs_one_500_completion_when_application_raises() -> None:
    records: list[loguru.Record] = []

    async def failing_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    async def send(_message: Message) -> None:
        return None

    sink_id = logger.add(
        lambda message: records.append(message.record),
        filter=lambda record: record["message"] == "request.complete",
    )
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await RequestIDMiddleware(failing_app, static_routes=frozenset())(
                _scope(), _receive, send
            )
    finally:
        logger.remove(sink_id)

    assert len(records) == 1
    assert records[0]["extra"]["status_code"] == 500
    assert records[0]["extra"]["request_route"] == "unmatched"
