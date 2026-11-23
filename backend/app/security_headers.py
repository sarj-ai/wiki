from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


_DEFAULT_HEADERS = (
    (b"cache-control", b"private, no-store"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
    (b"cross-origin-resource-policy", b"same-site"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-content-type-options", b"nosniff"),
)
_HSTS = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
# Redirects stay without COOP: MCP clients open the OAuth flow in a popup and a
# COOP header anywhere in its redirect chain would sever their opener reference.
_COOP = (b"cross-origin-opener-policy", b"same-origin")
_REDIRECT_STATUSES = range(300, 400)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, enable_hsts: bool) -> None:
        self._app = app
        self._headers = (*_DEFAULT_HEADERS, *((_HSTS,) if enable_hsts else ()))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _value in headers}
                defaults = (
                    self._headers
                    if message["status"] in _REDIRECT_STATUSES
                    else (*self._headers, _COOP)
                )
                headers.extend(header for header in defaults if header[0] not in existing)
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, send_with_headers)
