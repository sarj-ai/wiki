from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.document_transform.limits import MAX_MARKDOWN_BYTES


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_REQUEST_BODY_BYTES = 1_048_576
MAX_MCP_REQUEST_BODY_BYTES = 6 * MAX_MARKDOWN_BYTES + 65_536

_NO_STORE_HEADERS = {"Cache-Control": "private, no-store"}


class _RequestBodyTooLargeError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=413,
            detail="Request body is too large",
            headers=_NO_STORE_HEADERS,
        )


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int = MAX_REQUEST_BODY_BYTES,
        path_limits: Mapping[str, int] | None = None,
    ) -> None:
        if max_bytes <= 0:
            msg = "max_bytes must be positive"
            raise ValueError(msg)
        if path_limits and any(limit <= 0 for limit in path_limits.values()):
            msg = "path limits must be positive"
            raise ValueError(msg)
        self._app = app
        self._max_bytes = max_bytes
        self._path_limits = dict(path_limits or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        max_bytes = self._path_limits.get(scope["path"], self._max_bytes)
        declared_size = _declared_size(scope)
        if declared_size is None:
            await _reject(scope, receive, send, status_code=400, detail="Invalid Content-Length")
            return
        if declared_size > max_bytes:
            await _reject(
                scope,
                receive,
                send,
                status_code=413,
                detail="Request body is too large",
            )
            return

        received = 0
        response_started = False

        async def receive_limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise _RequestBodyTooLargeError
            return message

        async def send_tracked(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, receive_limited, send_tracked)
        except _RequestBodyTooLargeError:
            if response_started:
                raise
            await _reject(
                scope,
                receive,
                send,
                status_code=413,
                detail="Request body is too large",
            )


def _declared_size(scope: Scope) -> int | None:
    values = [value for name, value in scope.get("headers", []) if name == b"content-length"]
    if not values:
        return 0
    if any(not value.isdigit() for value in values):
        return None
    try:
        sizes = {int(value) for value in values}
    except ValueError:
        return None
    return sizes.pop() if len(sizes) == 1 else None


async def _reject(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    detail: str,
) -> None:
    response = JSONResponse(
        {"detail": detail},
        status_code=status_code,
        headers=_NO_STORE_HEADERS,
    )
    await response(scope, receive, send)
