from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


_AUTH_COOKIE_NAMES = frozenset({"access_token", "refresh_token"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CookieOriginMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_origin: str) -> None:
        self._app = app
        self._allowed_origin = allowed_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _UNSAFE_METHODS:
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        uses_auth_cookie = any(name in request.cookies for name in _AUTH_COOKIE_NAMES)
        if not uses_auth_cookie or request.headers.get("origin") == self._allowed_origin:
            await self._app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=403,
            content={
                "code": "csrf_origin_invalid",
                "detail": "Request origin is not allowed",
            },
        )
        await response(scope, receive, send)
