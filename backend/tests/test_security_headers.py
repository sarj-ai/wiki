from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from fastapi import FastAPI, Response
from fastapi.responses import RedirectResponse
from httpx import ASGITransport, AsyncClient
import pytest

from app.security_headers import SecurityHeadersMiddleware


if TYPE_CHECKING:
    import httpx


_API_CSP = "default-src 'none'; frame-ancestors 'none'"


class _Fetch(Protocol):
    async def __call__(self, path: str, *, enable_hsts: bool = False) -> httpx.Response: ...


def _app(*, enable_hsts: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=enable_hsts)

    @app.get("/default")
    async def default() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/cacheable")
    async def cacheable() -> Response:
        return Response(headers={"Cache-Control": "private, max-age=30"})

    @app.get("/redirect")
    async def redirect() -> RedirectResponse:
        return RedirectResponse("https://accounts.example/authorize")

    return app


@pytest.fixture
def fetch() -> _Fetch:
    async def run(path: str, *, enable_hsts: bool = False) -> httpx.Response:
        async with AsyncClient(
            transport=ASGITransport(app=_app(enable_hsts=enable_hsts)),
            base_url="https://test" if enable_hsts else "http://test",
        ) as client:
            return await client.get(path)

    return run


async def test_adds_private_browser_security_defaults(fetch: _Fetch) -> None:
    response = await fetch("/default")

    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Content-Security-Policy"] == _API_CSP
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-site"
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in response.headers


async def test_preserves_explicit_cache_policy(fetch: _Fetch) -> None:
    response = await fetch("/cacheable")

    assert response.headers["Cache-Control"] == "private, max-age=30"


async def test_redirects_keep_the_oauth_popup_opener(fetch: _Fetch) -> None:
    response = await fetch("/redirect")

    assert response.status_code == 307
    assert "Cross-Origin-Opener-Policy" not in response.headers
    assert response.headers["Content-Security-Policy"] == _API_CSP


async def test_enables_hsts_only_for_https_deployments(fetch: _Fetch) -> None:
    response = await fetch("/default", enable_hsts=True)

    assert response.headers["Strict-Transport-Security"] == ("max-age=31536000; includeSubDomains")


def test_security_headers_are_outermost_application_middleware() -> None:
    from app.main import app

    assert app.user_middleware[0].cls is SecurityHeadersMiddleware
