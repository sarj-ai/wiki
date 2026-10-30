from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
import pytest
from starlette.responses import Response, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.request_body import (
    MAX_MCP_REQUEST_BODY_BYTES,
    MAX_REQUEST_BODY_BYTES,
    RequestBodyLimitMiddleware,
)


@pytest.fixture
def limited_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=8)

    @app.post("/echo")
    async def echo(request: Request) -> Response:
        return Response(await request.body())

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"one"
            yield b"two"

        return StreamingResponse(chunks())

    return app


@pytest.fixture
async def client(limited_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=limited_app),
        base_url="http://test",
    ) as value:
        yield value


async def test_accepts_body_at_exact_limit(client: AsyncClient) -> None:
    response = await client.post("/echo", content=b"12345678")

    assert response.status_code == 200
    assert response.content == b"12345678"


async def test_mcp_path_limit_accepts_json_escape_expansion_and_rejects_larger_body() -> None:
    app = FastAPI()
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=8,
        path_limits={"/mcp": 20},
    )

    @app.post("/mcp")
    @app.post("/other")
    async def echo(request: Request) -> Response:
        return Response(await request.body())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/mcp", content=b"x" * 20)).status_code == 200
        assert (await client.post("/mcp", content=b"x" * 21)).status_code == 413
        assert (await client.post("/other", content=b"x" * 9)).status_code == 413


async def test_application_mcp_budget_covers_escaped_markdown() -> None:
    from app.main import app

    # The route may reject an unauthenticated caller, but the body limit must
    # allow a valid Markdown value before authentication or tool dispatch.
    body = (
        b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"wiki_import_markdown","arguments":{"request":{"content":"'
        + (b"\\u0001" * MAX_MARKDOWN_BYTES)
        + b'"}}}}'
    )
    assert MAX_REQUEST_BODY_BYTES < len(body) < MAX_MCP_REQUEST_BODY_BYTES
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/mcp", content=body)
    assert response.status_code != 413


async def test_does_not_interrupt_streaming_response(client: AsyncClient) -> None:
    response = await client.get("/stream")

    assert response.status_code == 200
    assert response.content == b"onetwo"


async def test_rejects_declared_body_over_limit_without_reading_it() -> None:
    receive_called = False

    async def receive() -> Message:
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"123456789"}

    messages = await _invoke(
        RequestBodyLimitMiddleware(_read_body_app, max_bytes=8),
        headers=[(b"content-length", b"9")],
        receive=receive,
    )

    assert _status(messages) == 413
    assert not receive_called


async def test_rejects_chunked_body_over_limit(client: AsyncClient) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"12345"
        yield b"6789"

    response = await client.post("/echo", content=chunks())

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large"}
    assert response.headers["Cache-Control"] == "private, no-store"


async def test_rejects_body_larger_than_declared_size() -> None:
    messages: deque[Message] = deque(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": False},
        ]
    )

    async def receive() -> Message:
        return messages.popleft()

    sent = await _invoke(
        RequestBodyLimitMiddleware(_read_body_app, max_bytes=8),
        headers=[(b"content-length", b"1")],
        receive=receive,
    )

    assert _status(sent) == 413


@pytest.mark.parametrize(
    "headers",
    [
        [(b"content-length", b"invalid")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"9" * 5_000)],
        [(b"content-length", b"1"), (b"content-length", b"2")],
    ],
    ids=["nonnumeric", "negative", "numeric-overflow", "conflicting-duplicates"],
)
async def test_rejects_invalid_content_length(headers: list[tuple[bytes, bytes]]) -> None:
    sent = await _invoke(
        RequestBodyLimitMiddleware(_read_body_app, max_bytes=8),
        headers=headers,
    )

    assert _status(sent) == 400
    assert _body(sent) == b'{"detail":"Invalid Content-Length"}'


async def test_allows_matching_duplicate_content_length() -> None:
    sent = await _invoke(
        RequestBodyLimitMiddleware(_read_body_app, max_bytes=8),
        headers=[(b"content-length", b"1"), (b"content-length", b"1")],
    )

    assert _status(sent) == 204


async def test_does_not_read_body_when_downstream_does_not_need_it() -> None:
    async def receive() -> Message:
        msg = "body must not be consumed"
        raise AssertionError(msg)

    sent = await _invoke(
        RequestBodyLimitMiddleware(_no_body_app, max_bytes=8),
        receive=receive,
    )

    assert _status(sent) == 204


async def test_passes_disconnect_through_unchanged() -> None:
    observed: Message | None = None

    async def observe_disconnect(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal observed
        del scope, send
        observed = await receive()

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    await _invoke(RequestBodyLimitMiddleware(observe_disconnect, max_bytes=8), receive=receive)

    assert observed == {"type": "http.disconnect"}


async def test_passes_non_http_scope_through() -> None:
    observed = False

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal observed
        del receive, send
        observed = scope["type"] == "lifespan"

    middleware = RequestBodyLimitMiddleware(inner, max_bytes=8)
    scope: Scope = {"type": "lifespan"}
    await middleware(scope, _unused_receive, _unused_send)

    assert observed


async def test_application_limit_preserves_cors_and_request_id() -> None:
    from app.main import app

    request_id = "oversize-request"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/not-a-route",
            content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
            headers={"Origin": settings.frontend_origin, "X-Request-ID": request_id},
        )

    assert response.status_code == 413
    assert response.headers["Access-Control-Allow-Origin"] == settings.frontend_origin
    assert "Origin" in response.headers["Vary"].split(", ")
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {"detail": "Request body is too large"}


async def test_application_rejects_chunked_body_consumed_by_fastapi() -> None:
    from app.main import app

    request_id = "chunked-oversize-request"

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"hidden_workspace_ids":["'
        yield b"x" * MAX_REQUEST_BODY_BYTES
        yield b'"]}'

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/extras/preferences",
            content=chunks(),
            headers={"Origin": settings.frontend_origin, "X-Request-ID": request_id},
        )

    assert response.status_code == 413
    assert response.headers["Access-Control-Allow-Origin"] == settings.frontend_origin
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Request-ID"] == request_id
    assert response.json() == {"detail": "Request body is too large"}


async def test_cookie_origin_guard_rejects_before_body_limit() -> None:
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.cookies.set("access_token", "opaque")
        response = await client.post(
            "/not-a-route",
            content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
            headers={"Origin": "https://hostile.example"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_origin_invalid"


def test_application_middleware_order() -> None:
    from app.main import app

    names = [_type_name(middleware.cls) for middleware in app.user_middleware]

    assert names.index("RequestIDMiddleware") < names.index("CookieOriginMiddleware")
    assert names.index("CookieOriginMiddleware") < names.index("CORSMiddleware")
    assert names.index("CORSMiddleware") < names.index("RequestBodyLimitMiddleware")


def test_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        RequestBodyLimitMiddleware(FastAPI(), max_bytes=0)


async def _invoke(
    app: ASGIApp,
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    receive: Receive | None = None,
) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    receive_callable = receive if receive is not None else _empty_receive
    await app(scope, receive_callable, send)
    return sent


async def _read_body_app(scope: Scope, receive: Receive, send: Send) -> None:
    del scope
    while True:
        message = await receive()
        if message["type"] == "http.disconnect" or not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _no_body_app(scope: Scope, receive: Receive, send: Send) -> None:
    del scope, receive
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _unused_receive() -> Message:
    msg = "receive must not be called"
    raise AssertionError(msg)


def _type_name(value: object) -> str:
    name = getattr(value, "__name__", None)
    assert isinstance(name, str)
    return name


async def _unused_send(message: Message) -> None:
    del message


def _status(messages: list[Message]) -> int:
    return next(
        message["status"] for message in messages if message["type"] == "http.response.start"
    )


def _body(messages: list[Message]) -> bytes:
    return b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
