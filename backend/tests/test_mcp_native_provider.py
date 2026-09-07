from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
import pytest

from app.mcp import native_provider
from app.mcp.native_provider import NativeWikiOAuthProvider
from app.mcp.native_request_context import NativeOAuthEndpoint, NativeOAuthRequestContext
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.schemas import McpCapability


_RESOURCE = "https://api.example.test/mcp"
_REGISTERED = "http://127.0.0.1:4567/callback"


class _Operations:
    @asynccontextmanager
    async def operation(self, _key: str) -> AsyncGenerator[object]:
        yield object()


class _State:
    intent: Any = None

    async def put(self, _db: object, _purpose: object, _key: str, value: Any, _expiry: Any) -> None:
        self.intent = value


class _Core:
    issuer = "https://api.example.test/"
    resource = _RESOURCE

    def __init__(self, registered: OAuthClientInformationFull) -> None:
        self.registered = registered
        self.operations = _Operations()
        self.state = _State()

    async def client(self, _db: object, _client_id: str) -> OAuthClientInformationFull:
        return self.registered


def _client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="client",
        redirect_uris=[AnyUrl(_REGISTERED)],
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=" ".join(McpCapability),
    )


@dataclass(frozen=True)
class _ProviderLab:
    provider: NativeWikiOAuthProvider
    core: _Core


def _provider(client: OAuthClientInformationFull) -> _ProviderLab:
    core = _Core(client)
    provider_core: Any = core

    async def admit() -> None:
        return None

    provider = NativeWikiOAuthProvider(
        core=provider_core,
        base_url="https://api.example.test",
        consent_url="https://wiki.example.test/mcp/consent",
        redirects=McpRedirectPolicy(loopback_paths=("/callback",)),
        request_guard=lambda _resource: NativeOAuthRequestContext(
            _RESOURCE, "a" * 64, NativeOAuthEndpoint("authorize")
        ),
        registration_admission=admit,
    )
    return _ProviderLab(provider, core)


def _params(callback: str) -> AuthorizationParams:
    return AuthorizationParams(
        state="client-state",
        scopes=list(McpCapability),
        code_challenge="a" * 43,
        redirect_uri=AnyUrl(callback),
        redirect_uri_provided_explicitly=True,
        resource=_RESOURCE,
    )


async def test_provider_authorize_accepts_codex_variable_loopback_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def database_time(_db: object) -> datetime:
        return datetime.now(UTC)

    monkeypatch.setattr(native_provider, "native_database_time", database_time)
    client = _client()
    lab = _provider(client)
    assert await lab.provider.authorize(client, _params("http://127.0.0.1:9876/callback"))
    assert lab.core.state.intent.redirect_uri == "http://127.0.0.1:9876/callback"
    assert lab.core.state.intent.scopes == list(McpCapability)


@pytest.mark.parametrize(
    "callback",
    ["http://localhost:9876/callback", "http://127.0.0.1:9876/other"],
)
async def test_provider_authorize_rejects_unregistered_loopback_host_or_path(
    monkeypatch: pytest.MonkeyPatch, callback: str
) -> None:
    async def database_time(_db: object) -> datetime:
        return datetime.now(UTC)

    monkeypatch.setattr(native_provider, "native_database_time", database_time)
    client = _client()
    lab = _provider(client)
    with pytest.raises(AuthorizeError):
        await lab.provider.authorize(client, _params(callback))
