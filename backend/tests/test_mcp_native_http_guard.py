from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import logging  # Assert SDK stdlib records are redacted only inside guarded OAuth requests.
import time
from typing import override
from urllib.parse import parse_qs, urlencode, urlsplit

from fastmcp.server.auth import AccessToken, OAuthProvider
import httpx
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
import pytest
from starlette.applications import Starlette

from app.mcp.native_admission import (
    RegistrationAdmissionDeniedError,
    RegistrationAdmissionUnavailableError,
)
from app.mcp.native_http_guard import NativeOAuthGuardSettings, NativeOAuthHTTPGuard
from app.mcp.native_request_context import (
    NativeOAuthEndpoint,
    NativeOAuthRequestContext,
    NativeOAuthRequestError,
    require_native_oauth_request,
)
from app.mcp.redirect_policy import McpRedirectPolicy


RESOURCE = "https://wiki.test/mcp"
CALLBACK = "http://127.0.0.1:4321/callback"
CANARY = "private-code-secret-canary"


class Provider(OAuthProvider):
    def __init__(self) -> None:
        super().__init__(
            base_url="https://wiki.test",
            revocation_options=RevocationOptions(enabled=True),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=["wiki:read", "wiki:transform"],
                default_scopes=["wiki:read"],
            ),
        )
        self.seen: list[NativeOAuthRequestContext] = []
        self.clients: list[OAuthClientInformationFull] = []
        self.fail = False
        self.issue_code = False
        self.exchanges = 0
        self.revoked: list[str] = []
        self.revocable = False
        self.authorization_redirect = "https://wiki.test/consent"
        self.registration_attempts = 0
        self.registration_retry: int | None = None
        self.registration_unavailable = False

    @override
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[AnyUrl(CALLBACK)],
            token_endpoint_auth_method="none",
            scope="wiki:read wiki:transform",
        )

    @override
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        self.seen.append(require_native_oauth_request(RESOURCE))
        if self.fail:
            raise RuntimeError(CANARY)
        return self.authorization_redirect

    @override
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        self.seen.append(require_native_oauth_request(RESOURCE))
        if self.fail:
            raise RuntimeError(CANARY)
        if self.issue_code:
            return AuthorizationCode(
                code=authorization_code,
                client_id=client.client_id or "client",
                scopes=["wiki:read"],
                expires_at=time.time() + 60,
                code_challenge=urlsafe_b64encode(hashlib.sha256(("a" * 43).encode()).digest())
                .rstrip(b"=")
                .decode(),
                redirect_uri=AnyUrl(CALLBACK),
                redirect_uri_provided_explicitly=True,
                resource=RESOURCE,
            )
        return None

    @override
    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.registration_attempts += 1
        if self.registration_retry is not None:
            raise RegistrationAdmissionDeniedError(timedelta(seconds=self.registration_retry))
        if self.registration_unavailable:
            raise RegistrationAdmissionUnavailableError
        self.clients.append(client_info)

    @override
    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        assert self.issue_code
        assert require_native_oauth_request(RESOURCE).endpoint is NativeOAuthEndpoint.TOKEN
        self.exchanges += 1
        return OAuthToken(
            access_token="synthetic-access", token_type="Bearer", expires_in=300, scope="wiki:read"
        )

    @override
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        self.seen.append(require_native_oauth_request(RESOURCE))
        return None

    @override
    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        pytest.fail("Unexpected refresh exchange")

    @override
    async def load_access_token(self, token: str) -> AccessToken | None:
        if self.revocable:
            return AccessToken(
                token=token,
                client_id="client",
                scopes=["wiki:read"],
                expires_at=int(time.time()) + 300,
            )
        return None

    @override
    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        assert require_native_oauth_request(RESOURCE).endpoint is NativeOAuthEndpoint.REVOKE
        self.revoked.append(token.token)


def client(provider: Provider) -> httpx.AsyncClient:
    app = NativeOAuthHTTPGuard(
        Starlette(routes=provider.get_routes()),
        NativeOAuthGuardSettings(
            issuer="https://wiki.test/",
            resource=RESOURCE,
            redirect_policy=McpRedirectPolicy(loopback_paths=("/callback",)),
        ),
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://wiki.test")


def authorize() -> dict[str, str]:
    return {
        "client_id": "client",
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "code_challenge": "a" * 43,
        "code_challenge_method": "S256",
        "scope": "wiki:read",
        "resource": RESOURCE,
    }


def exchange() -> dict[str, str]:
    return {
        "client_id": "client",
        "redirect_uri": CALLBACK,
        "grant_type": "authorization_code",
        "code": CANARY,
        "code_verifier": "a" * 43,
        "resource": RESOURCE,
    }


async def test_actual_sdk_authorize_receives_hashed_host_cookie_binding() -> None:
    provider = Provider()
    async with client(provider) as http:
        response = await http.get("/authorize", params=authorize())
        assert response.status_code == 302
        assert response.headers["location"] == "https://wiki.test/consent"
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Domain=" not in cookie
        assert "Path=/" in cookie
        nonce = http.cookies["__Host-wiki_mcp_nonce"]
        assert provider.seen[-1].browser_binding == hashlib.sha256(nonce.encode()).hexdigest()
        assert provider.seen[-1].endpoint == "authorize"
        await http.get("/authorize", params=authorize())
        assert provider.seen[0] == provider.seen[1]
    with pytest.raises(NativeOAuthRequestError):
        require_native_oauth_request(RESOURCE)


@pytest.mark.parametrize("resource", [None, "https://other.test/mcp"])
@pytest.mark.parametrize("grant", ["authorization_code", "refresh_token"])
async def test_token_resource_required_before_sdk(resource: str | None, grant: str) -> None:
    provider = Provider()
    values = (
        exchange()
        if grant == "authorization_code"
        else {
            "grant_type": grant,
            "refresh_token": CANARY,
            "client_id": "client",
            "resource": RESOURCE,
        }
    )
    if resource is None:
        values.pop("resource")
    else:
        values["resource"] = resource
    async with client(provider) as http:
        response = await http.post("/token", data=values)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert CANARY not in response.text
    assert provider.seen == []


async def test_actual_sdk_token_parser_keeps_guard_context() -> None:
    provider = Provider()
    async with client(provider) as http:
        response = await http.post("/token", data=exchange())
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_grant"
    assert provider.seen == [NativeOAuthRequestContext(RESOURCE, None, NativeOAuthEndpoint.TOKEN)]
    assert CANARY not in response.text


@pytest.mark.parametrize(
    "suffix", ["&resource=x", "&code=x", "&code_verifier=x", "&%63ode=x", "&x=%GG"]
)
async def test_duplicate_or_malformed_form_rejected(suffix: str) -> None:
    provider = Provider()
    async with client(provider) as http:
        response = await http.post(
            "/token",
            content=urlencode(exchange()) + suffix,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 400
    assert provider.seen == []


@pytest.mark.parametrize(
    "body",
    [
        '{"token_endpoint_auth_method":"none","token_endpoint_auth_method":"none"}',
        '{"x":' + "[" * 1000 + "0" + "]" * 1000 + "}",
        '{"client_name":"' + "x" * 17000 + '"}',
    ],
)
async def test_registration_rejects_raw_duplicates_depth_and_bytes(body: str) -> None:
    provider = Provider()
    async with client(provider) as http:
        response = await http.post(
            "/register", content=body, headers={"Content-Type": "application/json"}
        )
    assert response.status_code == 400
    assert provider.clients == []
    assert provider.registration_attempts == 0


async def test_actual_sdk_registration_public_only_no_remote_metadata() -> None:
    provider = Provider()
    values = {
        "redirect_uris": [CALLBACK],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": "Codex",
        "application_type": "native",
    }
    async with client(provider) as http:
        response = await http.post("/register", json=values)
        assert response.status_code == 201
        assert response.json().get("client_secret") is None
        assert len(provider.clients) == 1
        for changed in (
            {"token_endpoint_auth_method": "client_secret_post"},
            {"application_type": "web"},
            {"client_uri": "https://remote.test/metadata"},
            {"jwks_uri": "https://remote.test/keys"},
        ):
            denied = await http.post("/register", json={**values, **changed})
            assert denied.status_code == 400
    assert len(provider.clients) == 1


@pytest.mark.parametrize("retry", [1, 17, 1800])
async def test_actual_sdk_registration_admission_returns_retry_after(retry: int) -> None:
    provider = Provider()
    provider.registration_retry = retry
    async with client(provider) as http:
        response = await http.post(
            "/register",
            json={
                "redirect_uris": [CALLBACK],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": CANARY,
            },
        )
    assert response.status_code == 429
    assert response.headers["retry-after"] == str(retry)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"] == "temporarily_unavailable"
    assert CANARY not in response.text
    assert provider.registration_attempts == 1
    assert provider.clients == []


async def test_registration_admission_unavailable_does_not_disable_existing_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = Provider()
    provider.registration_unavailable = True
    provider.issue_code = True
    provider.revocable = True
    async with client(provider) as http:
        response = await http.post(
            "/register",
            json={
                "redirect_uris": [CALLBACK],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": CANARY,
            },
        )
        token = await http.post("/token", data=exchange())
        revoked = await http.post("/revoke", data={"client_id": "client", "token": CANARY})
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"] == "temporarily_unavailable"
    assert CANARY not in response.text
    assert CANARY not in caplog.text
    assert token.status_code == 200
    assert revoked.status_code == 200
    assert provider.registration_attempts == 1
    assert provider.clients == []


async def test_invalid_dcr_is_rejected_before_admission() -> None:
    provider = Provider()
    provider.registration_retry = 60
    async with client(provider) as http:
        response = await http.post(
            "/register",
            content='{"client_name":"x","client_name":"y"}',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 400
    assert provider.registration_attempts == 0


async def test_actual_sdk_exception_log_is_redacted_only_in_guard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = Provider()
    provider.fail = True
    async with client(provider) as http:
        response = await http.get("/authorize", params=authorize())
    assert CANARY not in response.text
    assert CANARY not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    assert "OAuth handler failure" in caplog.text
    logging.getLogger("mcp.server.auth.handlers.authorize").warning("outside guarded request")
    assert "outside guarded request" in caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("code_challenge_method", "plain"),
        ("code_challenge", "short"),
        ("code_challenge", "a" * 42 + "="),
        ("client_id", "https://remote.test/metadata"),
        ("resource", "https://other.test/mcp"),
        ("scope", CANARY),
    ],
)
async def test_authorize_rejects_invalid_security_fields(field: str, value: str) -> None:
    provider = Provider()
    async with client(provider) as http:
        response = await http.get("/authorize", params={**authorize(), field: value})
    assert response.status_code == 400
    assert CANARY not in response.text
    assert provider.seen == []


@pytest.mark.parametrize("value", ["short", "a" * 129, "a" * 42 + "="])
async def test_pkce_verifier_bounds_before_sdk(value: str) -> None:
    provider = Provider()
    async with client(provider) as http:
        response = await http.post("/token", data={**exchange(), "code_verifier": value})
    assert response.status_code == 400
    assert provider.seen == []


async def test_duplicate_authorize_query_and_browser_cookies_are_rejected() -> None:
    provider = Provider()
    async with client(provider) as http:
        response = await http.get("/authorize?" + urlencode(authorize()) + "&resource=duplicate")
        assert response.status_code == 400
        response = await http.get(
            "/authorize",
            params=authorize(),
            headers={
                "Cookie": "__Host-wiki_mcp_nonce="
                + "a" * 43
                + "; __Host-wiki_mcp_nonce="
                + "b" * 43
            },
        )
        assert response.status_code == 400
    assert provider.seen == []


def test_missing_or_mismatched_resource_context_fails_closed() -> None:
    with pytest.raises(NativeOAuthRequestError):
        require_native_oauth_request("https://other.test/mcp")


def test_insecure_host_cookie_configuration_rejected() -> None:
    with pytest.raises(NativeOAuthRequestError):
        NativeOAuthGuardSettings(
            issuer="https://wiki.test/",
            resource=RESOURCE,
            redirect_policy=McpRedirectPolicy(),
            cookie_secure=False,
        )


async def test_actual_sdk_pkce_verifies_before_exchange() -> None:
    provider = Provider()
    provider.issue_code = True
    async with client(provider) as http:
        denied = await http.post("/token", data={**exchange(), "code_verifier": "b" * 43})
        assert denied.json()["error"] == "invalid_grant"
        assert provider.exchanges == 0
        accepted = await http.post("/token", data=exchange())
    assert accepted.status_code == 200
    assert accepted.json()["scope"] == "wiki:read"
    assert provider.exchanges == 1


async def test_unexpected_token_provider_failure_is_private(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = Provider()
    provider.fail = True
    async with client(provider) as http:
        response = await http.post("/token", data=exchange())
    assert response.status_code == 500
    assert response.json() == {"error": "server_error", "error_description": "OAuth request failed"}
    assert CANARY not in caplog.text
    with pytest.raises(NativeOAuthRequestError):
        require_native_oauth_request(RESOURCE)


@pytest.mark.parametrize("explicit_empty", [False, True])
async def test_actual_sdk_public_revocation_accepts_no_secret(explicit_empty: bool) -> None:
    provider = Provider()
    provider.revocable = True
    values = {"client_id": "client", "token": CANARY}
    if explicit_empty:
        values["client_secret"] = ""
    async with client(provider) as http:
        response = await http.post("/revoke", data=values)
    assert response.status_code == 200
    assert response.content == b""
    assert provider.revoked == [CANARY]


async def test_public_revocation_rejects_confidential_secret_and_unknown_token_is_success() -> None:
    provider = Provider()
    async with client(provider) as http:
        denied = await http.post(
            "/revoke", data={"client_id": "client", "token": CANARY, "client_secret": CANARY}
        )
        assert denied.status_code == 400
        unknown = await http.post("/revoke", data={"client_id": "client", "token": CANARY})
        assert unknown.status_code == 200
    assert provider.revoked == []


async def test_actual_sdk_authorization_error_redirect_includes_exact_issuer() -> None:
    provider = Provider()
    provider.fail = True
    async with client(provider) as http:
        response = await http.get(
            "/authorize", params={**authorize(), "state": "opaque-client-state"}
        )
    location = urlsplit(response.headers["location"])
    assert location.scheme == "http"
    assert location.netloc == "127.0.0.1:4321"
    assert parse_qs(location.query) == {
        "error": ["server_error"],
        "error_description": ["OAuth request failed"],
        "state": ["opaque-client-state"],
        "iss": ["https://wiki.test/"],
    }


@pytest.mark.parametrize(
    "suffix",
    [
        "&iss=https://wrong.test/",
        "&iss=https://wiki.test/&iss=https://wiki.test/",
        "&error=access_denied",
    ],
)
async def test_existing_conflicting_or_duplicate_issuer_never_overwritten(suffix: str) -> None:
    provider = Provider()
    provider.authorization_redirect = f"{CALLBACK}?code=synthetic-code{suffix}"
    async with client(provider) as http:
        response = await http.get("/authorize", params=authorize())
    assert response.status_code == 500
    assert "synthetic-code" not in response.text
    assert "location" not in response.headers


async def test_final_registered_code_redirect_gets_issuer_only_once() -> None:
    provider = Provider()
    provider.authorization_redirect = f"{CALLBACK}?code=synthetic-code&iss=https://wiki.test/"
    async with client(provider) as http:
        response = await http.get("/authorize", params=authorize())
    assert parse_qs(urlsplit(response.headers["location"]).query) == {
        "code": ["synthetic-code"],
        "iss": ["https://wiki.test/"],
    }


@pytest.mark.parametrize(
    ("extra_headers", "expected_reads", "expected_provider_reads"),
    [
        ([(b"Content-Type", b"application/x-www-form-urlencoded")], 0, 0),
        ([(b"AuThOrIzAtIoN", b"Bearer synthetic")], 0, 0),
        ([(b"Cookie", b"one"), (b"cookie", b"two")], 0, 0),
        ([(b"host", b"wiki.test"), (b"Host", b"wiki.test")], 1, 1),
        ([(f"x-extra-{index}".encode(), b"x") for index in range(63)], 1, 1),
        ([(f"x-extra-{index}".encode(), b"x") for index in range(64)], 0, 0),
    ],
    ids=[
        "mixed-type-duplicate",
        "mixed-bearer",
        "mixed-cookie-duplicate",
        "ordinary-duplicates",
        "64-headers",
        "65-headers",
    ],
)
async def test_native_header_boundary_uses_original_case_sensitive_content_type(
    extra_headers: list[tuple[bytes, bytes]],
    expected_reads: int,
    expected_provider_reads: int,
) -> None:
    headers = [(b"content-type", b"application/x-www-form-urlencoded"), *extra_headers]
    result = await _raw_exchange(headers)
    assert result.status == (401 if expected_provider_reads else 400)
    assert result.reads == expected_reads
    assert result.provider_reads == expected_provider_reads


@pytest.mark.parametrize(("extra_bytes", "expected_reads"), [(0, 1), (1, 0)])
async def test_native_header_total_byte_budget_is_exact(
    extra_bytes: int, expected_reads: int
) -> None:
    content_type = (b"content-type", b"application/x-www-form-urlencoded")
    available = 16_384 - sum(len(part) for part in content_type) - len(b"x")
    result = await _raw_exchange([content_type, (b"x", b"x" * (available + extra_bytes))])
    assert result.status == (400 if extra_bytes else 401)
    assert result.reads == expected_reads
    assert result.provider_reads == expected_reads


async def test_uppercase_only_content_type_keeps_native_exact_name_lookup() -> None:
    result = await _raw_exchange([(b"Content-Type", b"application/x-www-form-urlencoded")])
    assert result.status == 400
    assert result.reads == 1
    assert result.provider_reads == 0


@dataclass(frozen=True, slots=True)
class _ExchangeProbe:
    status: int
    reads: int
    provider_reads: int


async def _raw_exchange(headers: list[tuple[bytes, bytes]]) -> _ExchangeProbe:
    from starlette.types import Message, Scope

    provider = Provider()
    guarded = NativeOAuthHTTPGuard(
        Starlette(routes=provider.get_routes()),
        NativeOAuthGuardSettings(
            issuer="https://wiki.test/",
            resource=RESOURCE,
            redirect_policy=McpRedirectPolicy(loopback_paths=("/callback",)),
        ),
    )
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "https",
        "method": "POST",
        "path": "/token",
        "raw_path": b"/token",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "server": ("wiki.test", 443),
        "client": ("127.0.0.1", 4321),
    }
    reads = 0
    status = 0

    async def receive() -> Message:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": urlencode(exchange()).encode()}

    async def send(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]

    await guarded(scope, receive, send)
    return _ExchangeProbe(status=status, reads=reads, provider_reads=len(provider.seen))


@pytest.mark.parametrize("overflow", [0, 1, 1_048_576])
async def test_native_header_name_budget_is_bounded_before_body_read(overflow: int) -> None:
    content_type = (b"content-type", b"application/x-www-form-urlencoded")
    available = 16_384 - sum(len(part) for part in content_type)
    result = await _raw_exchange([content_type, (b"X" * (available + overflow), b"")])
    assert result.status == (400 if overflow else 401)
    assert result.reads == (0 if overflow else 1)
    assert result.provider_reads == result.reads
