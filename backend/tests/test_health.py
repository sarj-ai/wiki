from __future__ import annotations

from types import TracebackType

import httpx
import pytest

from app import main


class _Session:
    def __init__(self, *, fail: bool) -> None:
        self._fail = fail

    async def execute(self, _statement: object) -> None:
        if self._fail:
            msg = "database unavailable"
            raise RuntimeError(msg)


class _SessionContext:
    def __init__(self, *, fail: bool) -> None:
        self._session = _Session(fail=fail)

    async def __aenter__(self) -> _Session:
        return self._session

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_health_reports_database_and_release_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "async_session", lambda: _SessionContext(fail=False))
    response = await main.health_check()
    assert response == {"database": "ok", "revision": main.settings.RELEASE_SHA, "status": "ok"}


@pytest.mark.asyncio
async def test_health_keeps_release_identity_when_database_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "async_session", lambda: _SessionContext(fail=True))
    response = await main.health_check()
    assert response == {
        "database": "unavailable",
        "revision": main.settings.RELEASE_SHA,
        "status": "degraded",
    }


@pytest.mark.asyncio
async def test_cors_allows_and_exposes_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "async_session", lambda: _SessionContext(fail=False))
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.example.test"
    ) as client:
        preflight = await client.options(
            "/health",
            headers={
                "Access-Control-Request-Headers": "x-request-id",
                "Access-Control-Request-Method": "GET",
                "Origin": main.settings.frontend_origin,
            },
        )
        assert preflight.status_code == 200
        assert "x-request-id" in preflight.headers["access-control-allow-headers"].lower()

        response = await client.get(
            "/health",
            headers={"Origin": main.settings.frontend_origin, "X-Request-ID": "wiki-cors-check"},
        )
        assert response.headers["x-request-id"] == "wiki-cors-check"
        assert "x-request-id" in response.headers["access-control-expose-headers"].lower()
