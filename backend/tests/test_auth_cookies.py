from __future__ import annotations

from fastapi import Response
from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import auth_service_dep
from app.auth.errors import AuthFailedError
from app.auth.router import clear_auth_cookies, set_auth_cookies
from app.auth.service import AuthSession
from app.config import settings


def _session() -> AuthSession:
    return AuthSession(jwt_access="access.jwt", jwt_refresh="refresh.jwt")


def _set_cookie_headers(response: Response) -> list[str]:
    return [v.decode() for k, v in response.raw_headers if k == b"set-cookie"]


def _deletions(headers: list[str]) -> list[str]:
    return [h for h in headers if "Max-Age=0" in h]


def test_default_no_domain_attribute() -> None:
    response = Response()
    set_auth_cookies(response, _session())
    headers = _set_cookie_headers(response)
    assert len(headers) == 3
    assert all("Domain=" not in h for h in headers)


def test_session_exp_cookie_is_js_readable_and_numeric() -> None:
    response = Response()
    set_auth_cookies(response, _session())
    exp = [h for h in _set_cookie_headers(response) if h.startswith("session_exp=")]
    assert len(exp) == 1
    assert "HttpOnly" not in exp[0]
    value = int(exp[0].split("session_exp=")[1].split(";")[0])
    assert value > 0


def test_domain_set_emits_domain_pair_and_legacy_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "COOKIE_DOMAIN", "sarj.ai")
    response = Response()
    set_auth_cookies(response, _session())
    headers = _set_cookie_headers(response)
    assert len(headers) == 6
    deletions = _deletions(headers)
    assert len(deletions) == 3
    assert all("Domain=" not in h for h in deletions)
    sets = [h for h in headers if h not in deletions]
    assert len(sets) == 3
    assert all("Domain=sarj.ai" in h for h in sets)


def test_clear_default_deletes_host_only_pair() -> None:
    response = Response()
    clear_auth_cookies(response)
    headers = _set_cookie_headers(response)
    assert len(headers) == 3
    assert all("Domain=" not in h for h in headers)


def test_clear_with_domain_deletes_both_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COOKIE_DOMAIN", "sarj.ai")
    response = Response()
    clear_auth_cookies(response)
    headers = _set_cookie_headers(response)
    assert len(headers) == 6
    assert sum("Domain=sarj.ai" in h for h in headers) == 3


def test_settings_read_at_call_time_not_module_import(monkeypatch: pytest.MonkeyPatch) -> None:
    response_before = Response()
    set_auth_cookies(response_before, _session())
    monkeypatch.setattr(settings, "COOKIE_DOMAIN", "example.com")
    response_after = Response()
    set_auth_cookies(response_after, _session())
    assert all("Domain=" not in h for h in _set_cookie_headers(response_before))
    assert any("Domain=example.com" in h for h in _set_cookie_headers(response_after))


async def test_oauth_state_cookie_never_carries_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COOKIE_DOMAIN", "sarj.ai")
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/login", follow_redirects=False)
    state_cookies = [
        v for k, v in response.headers.multi_items() if k == "set-cookie" and "oauth_state=" in v
    ]
    assert len(state_cookies) == 2
    assert all("Domain=" not in cookie for cookie in state_cookies)
    active = [cookie for cookie in state_cookies if "Max-Age=600" in cookie]
    assert len(active) == 1
    assert "Path=/auth/callback" in active[0]


async def test_oauth_callback_clears_state_cookie_after_failure() -> None:
    from app.main import app

    class _RejectingAuth:
        async def exchange_code_for_session(
            self, code: str, state: str, expected_state: str | None
        ) -> AuthSession:
            del code, state, expected_state
            raise AuthFailedError

    app.dependency_overrides[auth_service_dep] = _RejectingAuth
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"oauth_state": "b" * 43},
    ) as client:
        response = await client.get(
            "/auth/callback",
            params={"code": "code", "state": "a" * 43},
            follow_redirects=False,
        )
    app.dependency_overrides.clear()

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    assert any(
        "oauth_state=" in cookie and "Max-Age=0" in cookie and "Path=/auth/callback" in cookie
        for cookie in response.headers.get_list("set-cookie")
    )
