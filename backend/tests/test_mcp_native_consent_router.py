from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from typing import NamedTuple, override
from urllib.parse import urlencode
import uuid

from fastapi import FastAPI
import httpx
import pytest

from app.auth.errors import TokenRevokedError
from app.auth.service import AuthService, AuthSession
from app.csrf import CookieOriginMiddleware
from app.identifiers import UserId
from app.mcp.identity import McpClientId
from app.mcp.native_consent_router import NativeConsentSettings, create_native_consent_router
from app.mcp.native_core import NativeOAuthCore
from app.mcp.native_schemas import (
    ConnectionView,
    ConsentStatus,
    ConsentView,
    NativeOAuthError,
    WikiConsentIdentity,
)
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.schemas import McpCapability
from app.models.mcp_grant import McpGrantId
from app.models.user import User


ORIGIN = "https://wiki.test"
ISSUER = "https://api.test/"
NONCE = "a" * 43
INTENT = "wm_intent_" + "a" * 43
CSRF = "wm_intent_" + "b" * 43
CODE = "wm_code_" + "c" * 43
PATH = f"/integrations/mcp/consent/{INTENT}"
CALLBACK = "http://127.0.0.1:4321/callback"


class Auth(AuthService):
    def __init__(self) -> None:
        self.user = User(id=UserId(uuid.uuid7()), email="person@wiki.test", token_version=0)
        self.resolved: list[str] = []
        self.revoked = False
        self.unavailable = False

    @override
    async def resolve_current_user(self, jwt_token: str) -> User:
        self.resolved.append(jwt_token)
        if self.revoked:
            raise TokenRevokedError
        if self.unavailable:
            msg = "private-session-canary"
            raise RuntimeError(msg)
        return self.user

    @override
    async def exchange_code_for_session(
        self, code: str, state: str, expected_state: str | None
    ) -> AuthSession:
        pytest.fail("Unexpected Google exchange")

    @override
    async def refresh_session(self, refresh_token: str) -> AuthSession:
        pytest.fail("Unexpected session refresh")

    @override
    async def revoke_session(self, access_token: str | None) -> None:
        pytest.fail("Unexpected global Wiki logout")


class Core(NativeOAuthCore):
    def __init__(self, user: User) -> None:
        self.user_id = user.id
        self.grant_id = McpGrantId(uuid.uuid7())
        self.decisions = 0
        self.denied = False
        self.reads = 0
        self.disconnected: list[McpGrantId] = []
        self.bad_redirect: str | None = None

    def check(self, identity: WikiConsentIdentity, browser: str) -> None:
        if (
            identity.user_id != self.user_id
            or identity.session_revision != 0
            or browser != hashlib.sha256(NONCE.encode()).hexdigest()
        ):
            raise NativeOAuthError

    @override
    async def consent_view(
        self, key: str, *, identity: WikiConsentIdentity, browser: str
    ) -> ConsentView:
        self.check(identity, browser)
        self.reads += 1
        return ConsentView(
            client_id=McpClientId("client"),
            client_name="Untrusted Client",
            scopes=[McpCapability.READ, McpCapability.TRANSFORM],
            csrf=CSRF,
            status="denied" if self.denied else "approved" if self.decisions else "pending",
        )

    @override
    async def consent_status(
        self, key: str, *, identity: WikiConsentIdentity, browser: str
    ) -> ConsentStatus:
        self.check(identity, browser)
        return ConsentStatus(
            status="denied" if self.denied else "approved" if self.decisions else "pending",
            restart_required=bool(self.decisions),
        )

    @override
    async def decide(
        self,
        key: str,
        *,
        identity: WikiConsentIdentity,
        browser: str,
        csrf: str,
        approve: bool,
        transform_approved: bool = False,
    ) -> str:
        self.check(identity, browser)
        if self.decisions or csrf != CSRF:
            raise NativeOAuthError
        self.decisions += 1
        self.denied = not approve
        if self.bad_redirect:
            return self.bad_redirect
        return f"{CALLBACK}?{urlencode({'code': CODE, 'iss': ISSUER} if approve else {'error': 'access_denied', 'iss': ISSUER})}"

    @override
    async def list_connections(self, identity: WikiConsentIdentity) -> list[ConnectionView]:
        if identity.user_id != self.user_id:
            raise NativeOAuthError
        return [
            ConnectionView(
                grant_id=self.grant_id,
                client_id=McpClientId("client"),
                capabilities=[McpCapability.READ],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        ]

    @override
    async def disconnect(self, identity: WikiConsentIdentity, grant_id: McpGrantId) -> None:
        if identity.user_id != self.user_id or grant_id != self.grant_id:
            raise NativeOAuthError
        self.disconnected.append(grant_id)


class Harness(NamedTuple):
    auth: Auth
    core: Core
    http: httpx.AsyncClient


def harness(*, access_cookie: bool = True, nonce: str = NONCE) -> Harness:
    auth = Auth()
    core = Core(auth.user)
    app = FastAPI()
    app.include_router(
        create_native_consent_router(
            settings=NativeConsentSettings(
                frontend_origin=ORIGIN,
                issuer=ISSUER,
                redirect_policy=McpRedirectPolicy(loopback_paths=("/callback",)),
            ),
            core_dependency=lambda: core,
            auth_dependency=lambda: auth,
        )
    )
    app.add_middleware(CookieOriginMiddleware, allowed_origin=ORIGIN)
    cookies = {"__Host-wiki_mcp_nonce": nonce}
    if access_cookie:
        cookies["access_token"] = "wiki-session"
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://api.test",
        headers={"Origin": ORIGIN},
        cookies=cookies,
    )
    return Harness(auth, core, http)


async def test_fresh_cookie_identity_and_account_view_never_approves() -> None:
    auth, core, http = harness()
    async with http:
        first = await http.get(PATH)
        assert first.status_code == 200
        assert first.json()["account_email"] == "person@wiki.test"
        assert first.json()["csrf"] == CSRF
        assert first.headers["cache-control"] == "no-store"
        assert first.headers["referrer-policy"] == "no-referrer"
        assert first.headers["content-security-policy"] == "frame-ancestors 'none'"
        auth.revoked = True
        assert (await http.get(PATH)).status_code == 401
    assert auth.resolved == ["wiki-session", "wiki-session"]
    assert core.decisions == 0


@pytest.mark.parametrize(
    "origin", [None, "null", "https://evil.test", "https://wiki.test.evil.test"]
)
async def test_hostile_origin_rejected_before_auth(origin: str | None) -> None:
    auth, core, http = harness()
    async with http:
        http.headers.pop("Origin")
        if origin is not None:
            http.headers["Origin"] = origin
        assert (await http.get(PATH)).status_code == 403
        assert (await http.post(PATH, json={"csrf": CSRF, "approve": True})).status_code == 403
    assert auth.resolved == []
    assert core.decisions == 0


async def test_bearer_only_is_not_browser_identity() -> None:
    auth, core, http = harness(access_cookie=False)
    async with http:
        response = await http.get(PATH, headers={"Authorization": "Bearer secret-canary"})
    assert response.status_code == 401
    assert auth.resolved == []
    assert core.reads == 0
    assert "secret-canary" not in response.text


async def test_cross_browser_cannot_view_or_decide() -> None:
    _auth, core, http = harness(nonce="b" * 43)
    async with http:
        assert (await http.get(PATH)).status_code == 400
        assert (await http.post(PATH, json={"csrf": CSRF, "approve": True})).status_code == 400
    assert core.decisions == 0


async def test_changed_account_or_session_revision_cannot_decide() -> None:
    auth, core, http = harness()
    async with http:
        assert (await http.get(PATH)).status_code == 200
        auth.user.token_version = 1
        assert (await http.post(PATH, json={"csrf": CSRF, "approve": True})).status_code == 400
        auth.user.token_version = 0
        auth.user.id = UserId(uuid.uuid7())
        assert (await http.get(PATH)).status_code == 400
    assert core.decisions == 0


async def test_cookie_identity_does_not_switch_to_bearer_identity() -> None:
    auth, _core, http = harness()
    async with http:
        response = await http.get(PATH, headers={"Authorization": "Bearer other-user"})
    assert response.status_code == 200
    assert auth.resolved == ["wiki-session"]


@pytest.mark.parametrize("approve", [True, False])
async def test_one_decision_and_terminal_status_has_no_code(approve: bool) -> None:
    _auth, core, http = harness()
    async with http:
        await http.get(PATH)
        response = await http.post(PATH, json={"csrf": CSRF, "approve": approve})
        assert response.status_code == 200
        assert response.json()["redirect_uri"].startswith(CALLBACK)
        assert (await http.post(PATH, json={"csrf": CSRF, "approve": approve})).status_code == 400
        status = await http.get(f"{PATH}/status")
        assert status.json() == {
            "status": "approved" if approve else "denied",
            "restart_required": True,
        }
        assert CODE not in status.text
    assert core.decisions == 1


@pytest.mark.parametrize(
    "body",
    [
        '{"csrf":"' + CSRF + '","approve":true,"approve":false}',
        '{"csrf":"' + CSRF + '","approve":"true"}',
        '{"csrf":"wrong","approve":true}',
        '{"csrf":"' + CSRF + '","approve":true,"owner":"other-user"}',
        '{"x":' + "[" * 1500 + "0" + "]" * 1500 + "}",
    ],
)
async def test_duplicate_or_invalid_decision_body_rejected(body: str) -> None:
    _auth, core, http = harness()
    async with http:
        response = await http.post(PATH, content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert core.decisions == 0


async def test_oversize_body_rejected_before_auth_and_pydantic() -> None:
    auth, core, http = harness()
    async with http:
        response = await http.post(
            PATH, content="x" * 4097, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 413
    assert auth.resolved == []
    assert core.decisions == 0


async def test_duplicate_cookie_or_query_is_rejected() -> None:
    auth, _core, http = harness()
    async with http:
        response = await http.get(
            PATH,
            headers={"Cookie": f"access_token=one;access_token=two;__Host-wiki_mcp_nonce={NONCE}"},
        )
        assert response.status_code == 400
        assert (await http.get(f"{PATH}?intent=other&intent=other")).status_code == 400
    assert auth.resolved == []


@pytest.mark.parametrize(
    "redirect",
    [
        "https://evil.test/?code=" + CODE,
        CALLBACK + "?code=" + CODE,
        CALLBACK + "?code=" + CODE + "&iss=https://wrong.test/",
        CALLBACK + "?code=" + CODE + "&code=" + CODE + "&iss=" + ISSUER,
    ],
)
async def test_outbound_redirect_rejected_without_code_disclosure(redirect: str) -> None:
    _auth, core, http = harness()
    core.bad_redirect = redirect
    async with http:
        response = await http.post(PATH, json={"csrf": CSRF, "approve": True})
    assert response.status_code == 400
    assert CODE not in response.text


async def test_disconnect_requires_csrf_without_global_logout() -> None:
    auth, core, http = harness()
    async with http:
        listed = await http.get("/integrations/mcp/connections")
        target = f"/integrations/mcp/connections/{core.grant_id}"
        assert (await http.delete(target)).status_code == 403
        accepted = await http.delete(target, headers={"X-MCP-CSRF": listed.json()["csrf"]})
        assert accepted.status_code == 204
    assert core.disconnected == [core.grant_id]
    assert auth.revoked is False


async def test_management_mints_missing_nonce_only_after_authentication() -> None:
    _auth, core, http = harness()
    http.cookies.delete("__Host-wiki_mcp_nonce")
    async with http:
        missing = await http.get(PATH)
        assert missing.status_code == 403
        assert "set-cookie" not in missing.headers
        listed = await http.get("/integrations/mcp/connections")
        assert listed.status_code == 200
        cookie = listed.headers["set-cookie"]
        assert "Secure" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Domain=" not in cookie
        deleted = await http.delete(
            f"/integrations/mcp/connections/{core.grant_id}",
            headers={"X-MCP-CSRF": listed.json()["csrf"]},
        )
        assert deleted.status_code == 204


async def test_unauthenticated_management_does_not_mint_nonce() -> None:
    _auth, _core, http = harness(access_cookie=False)
    http.cookies.delete("__Host-wiki_mcp_nonce")
    async with http:
        response = await http.get("/integrations/mcp/connections")
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def test_operational_auth_failure_is_sanitized_unavailable() -> None:
    auth, _core, http = harness()
    auth.unavailable = True
    async with http:
        response = await http.get(PATH)
    assert response.status_code == 503
    assert "private-session-canary" not in response.text
    assert "set-cookie" not in response.headers
