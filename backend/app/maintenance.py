from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


_HEALTH_PATH = "/health"
_RESPONSE_BODY = b'{"detail":"Temporarily unavailable during maintenance"}'


class MaintenanceModeMiddleware:
    def __init__(self, app: ASGIApp, *, enabled: bool) -> None:
        self._app = app
        self._enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._enabled or scope["type"] != "http" or scope.get("path") == _HEALTH_PATH:
            await self._app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"private, no-store"),
                    (b"retry-after", b"60"),
                    (b"content-length", str(len(_RESPONSE_BODY)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _RESPONSE_BODY})
