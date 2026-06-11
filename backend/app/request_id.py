"""Per-request correlation id.

``RequestIDMiddleware`` stamps every HTTP request with a short id —
honoring an inbound ``X-Request-ID`` from a proxy when present — and
stores it in a ``ContextVar`` that survives ``asyncio.to_thread`` hops,
so even Drive calls running on worker threads log under the same id.
The id is echoed back as a response header for client-side correlation.

``logging_config`` injects the id into every loguru record; grepping a
single request id reconstructs the full timeline of one API call.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING
from uuid import uuid4


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Default "-" keeps log formats stable for lines emitted outside any
# request (startup, background cache revalidation).
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_HEADER = b"x-request-id"


class RequestIDMiddleware:
    """Pure-ASGI middleware (no ``BaseHTTPMiddleware`` buffering)."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope) or uuid4().hex[:12]
        token = request_id_var.set(request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((_HEADER, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


def _inbound_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == _HEADER:
            # Cap length so a hostile header can't bloat every log line.
            decoded = value.decode("latin-1").strip()[:64]
            return decoded or None
    return None
