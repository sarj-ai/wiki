from __future__ import annotations

from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from app.security_headers import SecurityHeadersMiddleware


def _app(*, enable_hsts: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=enable_hsts)

    @app.get("/default")
    async def default() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/cacheable")
    async def cacheable() -> Response:
        return Response(headers={"Cache-Control": "private, max-age=30"})

    return app


async def test_adds_private_browser_security_defaults() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app(enable_hsts=False)),
        base_url="http://test",
    ) as client:
        response = await client.get("/default")

    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in response.headers


async def test_preserves_explicit_cache_policy() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app(enable_hsts=False)),
        base_url="http://test",
    ) as client:
        response = await client.get("/cacheable")

    assert response.headers["Cache-Control"] == "private, max-age=30"


async def test_enables_hsts_only_for_https_deployments() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app(enable_hsts=True)),
        base_url="https://test",
    ) as client:
        response = await client.get("/default")

    assert response.headers["Strict-Transport-Security"] == ("max-age=31536000; includeSubDomains")


def test_security_headers_are_outermost_application_middleware() -> None:
    from app.main import app

    names = [_type_name(middleware.cls) for middleware in app.user_middleware]

    assert names[0] == "SecurityHeadersMiddleware"


def _type_name(value: object) -> str:
    name = getattr(value, "__name__", None)
    assert isinstance(name, str)
    return name
