# pyright: reportPrivateUsage=false

from __future__ import annotations

from fastapi import Response
from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.router import _clear_auth_cookies, _set_auth_cookies
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
    _set_auth_cookies(response, _session())
    headers = _set_cookie_headers(response)
    assert len(headers) == 2
    assert all("Domain=" not in h for h in headers)


def test_domain_set_emits_domain_pair_and_legacy_deletions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "COOKIE_DOMAIN", "sarj.ai")
    response = Response()
    _set_auth_cookies(response, _session())
    headers = _set_cookie_headers(response)
    assert len(headers) == 4
    deletions = _deletions(headers)
    assert len(deletions) == 2
    assert all("Domain=" not in h for h in deletions)
    sets = [h for h in headers if h not in deletions]
    assert len(sets) == 2
    assert all("Domain=sarj.ai" in h for h in sets)


def test_clear_default_deletes_host_only_pair() -> None:
    response = Response()
    _clear_auth_cookies(response)
    headers = _set_cookie_headers(response)
    assert len(headers) == 2
    assert all("Domain=" not in h for h in headers)


def test_clear_with_domain_deletes_both_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COOKIE_DOMAIN", "sarj.ai")
    response = Response()
    _clear_auth_cookies(response)
    headers = _set_cookie_headers(response)
    assert len(headers) == 4
    assert sum("Domain=sarj.ai" in h for h in headers) == 2


def test_settings_read_at_call_time_not_module_import(monkeypatch: pytest.MonkeyPatch) -> None:
    response_before = Response()
    _set_auth_cookies(response_before, _session())
    monkeypatch.setattr(settings, "COOKIE_DOMAIN", "example.com")
    response_after = Response()
    _set_auth_cookies(response_after, _session())
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
    assert len(state_cookies) == 1
    assert "Domain=" not in state_cookies[0]
