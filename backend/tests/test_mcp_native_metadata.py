from typing import override
from urllib.parse import parse_qs, urlsplit

from fastmcp.server.auth import AccessToken, OAuthProvider
import httpx
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl
import pytest
from starlette.applications import Starlette

from app.mcp.native_metadata import canonical_issuer, issuer_redirect, native_metadata_routes
from app.mcp.native_request_context import NativeOAuthRequestError


_ISSUER = "https://wiki.test/"
_CALLBACK = "http://127.0.0.1:4321/callback"


class Provider(OAuthProvider):
    def __init__(self) -> None:
        super().__init__(
            base_url=_ISSUER,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=["wiki:read"], default_scopes=["wiki:read"]
            ),
            revocation_options=RevocationOptions(enabled=True),
        )

    @override
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[AnyUrl(_CALLBACK)],
            token_endpoint_auth_method="none",
            scope="wiki:read",
        )

    @override
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        error = "access_denied"
        raise AuthorizeError(error, "denied")

    @override
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        pytest.fail("Unexpected code lookup")

    @override
    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        pytest.fail("Unexpected code exchange")

    @override
    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        pytest.fail("Unexpected refresh lookup")

    @override
    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        pytest.fail("Unexpected refresh exchange")

    @override
    async def load_access_token(self, token: str) -> AccessToken | None:
        pytest.fail("Unexpected access lookup")

    @override
    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        pytest.fail("Unexpected registration")

    @override
    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        pytest.fail("Unexpected revocation")


@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_sdk_metadata_composition_is_explicit_and_public_only(enabled: bool) -> None:
    provider = Provider()
    original = provider.get_routes("/mcp")
    routes = native_metadata_routes(provider, original, issuer_responses_enabled=enabled)
    for old, new in zip(original, routes, strict=True):
        if old.path != "/.well-known/oauth-authorization-server":
            assert new is old
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=Starlette(routes=routes)), base_url=_ISSUER
    ) as client:
        response = await client.get("/.well-known/oauth-authorization-server")
        resource = await client.get("/.well-known/oauth-protected-resource/mcp")
    metadata = response.json()
    assert metadata["issuer"] == _ISSUER
    assert metadata["authorization_response_iss_parameter_supported"] is enabled
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["revocation_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["client_id_metadata_document_supported"] is False
    assert metadata["registration_endpoint"] == "https://wiki.test/register"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert resource.json()["authorization_servers"] == [_ISSUER]


async def test_sdk_authorization_error_redirect_can_be_bound_without_private_override() -> None:
    provider = Provider()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=Starlette(routes=provider.get_routes())),
        base_url=_ISSUER,
    ) as client:
        response = await client.get(
            "/authorize",
            params={
                "client_id": "client",
                "redirect_uri": _CALLBACK,
                "response_type": "code",
                "code_challenge": "a" * 43,
                "code_challenge_method": "S256",
                "scope": "wiki:read",
                "state": "opaque-state",
            },
        )
    assert response.status_code == 302
    bound = issuer_redirect(response.headers["location"], _ISSUER)
    fields = parse_qs(urlsplit(bound).query)
    assert fields["error"] == ["access_denied"]
    assert fields["state"] == ["opaque-state"]
    assert fields["iss"] == [_ISSUER]


@pytest.mark.parametrize("result", ["code=synthetic-code", "error=access_denied"])
def test_allow_and_deny_preserve_state_and_bind_exact_issuer_idempotently(result: str) -> None:
    bound = issuer_redirect(f"{_CALLBACK}?{result}&state=a%2Bb", "https://wiki.test")
    assert issuer_redirect(bound, _ISSUER) == bound
    assert parse_qs(urlsplit(bound).query)["state"] == ["a+b"]
    assert parse_qs(urlsplit(bound).query)["iss"] == [_ISSUER]


@pytest.mark.parametrize(
    "query",
    [
        "intent=not-an-authorization-result",
        "code=a&error=access_denied",
        "code=",
        "code=a&iss=https%3A%2F%2Fother.test%2F",
        "code=a&iss=https%3A%2F%2Fwiki.test%2F&iss=https%3A%2F%2Fwiki.test%2F",
        "code=a&state=x&state=y",
        "code=a#fragment",
    ],
)
def test_ambiguous_or_conflicting_issuer_redirect_rejected(query: str) -> None:
    with pytest.raises(NativeOAuthRequestError):
        issuer_redirect(f"{_CALLBACK}?{query}", _ISSUER)


@pytest.mark.parametrize(
    "issuer",
    ["http://wiki.test", "https://user@wiki.test", "https://wiki.test?q=x", "https://wiki.test/#x"],
)
def test_issuer_configuration_rejected(issuer: str) -> None:
    with pytest.raises(NativeOAuthRequestError):
        canonical_issuer(issuer)


def test_missing_metadata_route_and_split_issuer_fail_closed() -> None:
    provider = Provider()
    with pytest.raises(NativeOAuthRequestError):
        native_metadata_routes(provider, [])
    original = provider.get_routes()
    provider.issuer_url = AnyHttpUrl("https://other.test/")
    with pytest.raises(NativeOAuthRequestError):
        native_metadata_routes(provider, original)


async def test_composed_metadata_snapshot_and_recomposition_preserve_native_cors() -> None:
    provider = Provider()
    routes = native_metadata_routes(provider, provider.get_routes(), issuer_responses_enabled=True)
    transport = httpx.ASGITransport(app=Starlette(routes=routes))
    path = "/.well-known/oauth-authorization-server"
    async with httpx.AsyncClient(transport=transport, base_url=_ISSUER) as client:
        first = await client.get(path, headers={"Origin": "https://client.test"})
        options = provider.client_registration_options
        assert options is not None
        assert options.valid_scopes is not None
        options.valid_scopes.append("wiki:future-fixture")
        second = await client.get(path, headers={"Origin": "https://other.test"})
        preflight = await client.options(
            path,
            headers={
                "Origin": "https://client.test",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = await client.post(path)
    assert first.content == second.content
    assert first.headers["cache-control"] == "public, max-age=3600"
    assert first.headers["content-type"] == "application/json"
    assert first.headers["access-control-allow-origin"] == "*"
    assert preflight.status_code == 200
    assert "GET" in preflight.headers["access-control-allow-methods"]
    assert denied.status_code == 405
    rebuilt = native_metadata_routes(provider, provider.get_routes(), issuer_responses_enabled=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=Starlette(routes=rebuilt)), base_url=_ISSUER
    ) as client:
        third = await client.get(path)
    assert "wiki:future-fixture" not in first.json()["scopes_supported"]
    assert "wiki:future-fixture" in third.json()["scopes_supported"]


async def test_metadata_native_unicode_escaping_and_documentation_are_exact() -> None:
    from mcp.server.auth.handlers.metadata import MetadataHandler
    from mcp.server.auth.routes import build_metadata
    from starlette.requests import Request

    from app.mcp.native_metadata import NativeOAuthMetadata

    provider = Provider()
    options = provider.client_registration_options
    assert options is not None
    scopes = ["wiki:read", 'wikï:quote"slash\\tab\tnewline\nemoji🙂separator\u2028']
    options.valid_scopes = scopes
    provider.service_documentation_url = AnyHttpUrl("https://wiki.test/文档?emoji=🙂&name=a%20b")
    base = build_metadata(
        AnyHttpUrl(_ISSUER),
        provider.service_documentation_url,
        options,
        provider.revocation_options or RevocationOptions(),
    )
    expected_model = NativeOAuthMetadata.model_validate(
        {
            **base.model_dump(),
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": False,
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
        }
    )
    native_response = await MetadataHandler(expected_model).handle(Request({"type": "http"}))
    routes = native_metadata_routes(provider, provider.get_routes(), issuer_responses_enabled=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=Starlette(routes=routes)), base_url=_ISSUER
    ) as client:
        response = await client.get("/.well-known/oauth-authorization-server")
    assert response.content == native_response.body
    assert response.json()["scopes_supported"] == scopes
    assert response.json()["service_documentation"] == str(provider.service_documentation_url)
