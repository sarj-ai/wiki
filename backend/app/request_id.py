from __future__ import annotations

from contextvars import ContextVar
import re
import secrets
import time
from typing import TYPE_CHECKING

from loguru import logger
from starlette.routing import Route

from app.performance import (
    begin_request_metrics,
    reset_request_metrics,
    server_timing_value,
    snapshot,
)


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_HEADER = b"x-request-id"
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope) or secrets.token_hex(6)
        token = request_id_var.set(request_id)
        metrics_token = begin_request_metrics()
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((_HEADER, request_id.encode("latin-1")))
                timing = server_timing_value()
                if timing is not None:
                    headers.append((b"server-timing", timing.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_header)
        finally:
            logger.bind(
                duration_ms=round((time.perf_counter() - started_at) * 1000, 1),
                request_method=scope["method"],
                request_route=_route_template(scope),
                status_code=status_code,
                **snapshot(),
            ).info("request.complete")
            reset_request_metrics(metrics_token)
            request_id_var.reset(token)


def _inbound_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == _HEADER:
            decoded = value.decode("latin-1")
            return decoded if _VALID_REQUEST_ID.fullmatch(decoded) is not None else None
    return None


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    return route.path_format if isinstance(route, Route) else "unmatched"
