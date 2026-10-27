import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
import uuid

from fastmcp.server.auth import AccessToken
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identifiers import UserId
from app.mcp.context import McpAuthorizationError, ValidatedMcpPrincipal
from app.mcp.native_context import native_context_factory
from app.mcp.schemas import McpCapability


_USER = uuid.uuid7()
_OPAQUE = "synthetic-opaque-access"


def verified(*, revision: int = 7, transform: bool = False) -> AccessToken:
    return AccessToken(
        token=_OPAQUE,
        client_id="fresh-client",
        subject=str(_USER),
        scopes=["wiki:read", "wiki:transform"] if transform else ["wiki:read"],
        claims={"user_id": str(_USER), "user_session_revision": revision},
    )


@dataclass
class Provider:
    result: AccessToken | None
    calls: list[str] = field(default_factory=list)
    cancel: bool = False

    async def load_access_token(self, token: str) -> AccessToken | None:
        self.calls.append(token)
        if self.cancel:
            raise asyncio.CancelledError
        return self.result


@dataclass
class Contexts:
    principals: list[ValidatedMcpPrincipal] = field(default_factory=list)
    closed: int = 0


def factory(
    monkeypatch: pytest.MonkeyPatch,
    provider: Provider,
    contexts: Contexts,
    http: httpx.AsyncClient,
    *,
    incoming: AccessToken | None,
) -> Any:
    sessions = async_sessionmaker[AsyncSession]()
    monkeypatch.setattr("app.mcp.native_context.get_access_token", lambda: incoming)

    @asynccontextmanager
    async def open_context(
        principal: ValidatedMcpPrincipal, *, sessionmaker: object, http: httpx.AsyncClient
    ) -> AsyncGenerator[Any]:
        assert sessionmaker is sessions
        assert http is expected_http
        contexts.principals.append(principal)
        try:
            yield SimpleNamespace(principal=principal)
        finally:
            contexts.closed += 1

    expected_http = http
    monkeypatch.setattr("app.mcp.native_context.open_mcp_context", open_context)
    boundary_provider: Any = provider
    return native_context_factory(boundary_provider, sessions=sessions, http=http)


async def test_each_open_revalidates_opaque_token_and_ignores_forged_cached_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = AccessToken(
        token=_OPAQUE,
        client_id="forged-client",
        subject="forged-subject",
        scopes=["wiki:transform"],
        claims={"user_id": "not-a-uuid", "user_session_revision": "forged"},
    )
    provider, contexts = Provider(verified()), Contexts()
    async with httpx.AsyncClient() as http:
        open_context = factory(monkeypatch, provider, contexts, http, incoming=cached)
        async with open_context() as opened:
            assert opened.principal == ValidatedMcpPrincipal(
                UserId(_USER), 7, frozenset({McpCapability.READ})
            )
        provider.result = verified(revision=8, transform=True)
        async with open_context() as opened:
            assert opened.principal == ValidatedMcpPrincipal(
                UserId(_USER), 8, frozenset(McpCapability)
            )
        provider.result = None
        with pytest.raises(McpAuthorizationError):
            async with open_context():
                pytest.fail("Revoked token retained its cached principal")
    assert provider.calls == [_OPAQUE] * 3
    assert len(contexts.principals) == contexts.closed == 2


@pytest.mark.parametrize("missing", ["incoming", "verified"])
async def test_missing_or_revoked_token_denies_before_application_context(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    provider = Provider(None if missing == "verified" else verified())
    contexts = Contexts()
    async with httpx.AsyncClient() as http:
        open_context = factory(
            monkeypatch,
            provider,
            contexts,
            http,
            incoming=None if missing == "incoming" else verified(),
        )
        with pytest.raises(McpAuthorizationError):
            async with open_context():
                pytest.fail("Missing authorization yielded")
    assert provider.calls == ([] if missing == "incoming" else [_OPAQUE])
    assert contexts.principals == []


@pytest.mark.parametrize("subject", [None, str(uuid.uuid7())])
async def test_verified_subject_must_match_verified_user_claim(
    monkeypatch: pytest.MonkeyPatch, subject: str | None
) -> None:
    token = verified()
    token.subject = subject
    provider, contexts = Provider(token), Contexts()
    async with httpx.AsyncClient() as http:
        open_context = factory(monkeypatch, provider, contexts, http, incoming=verified())
        with pytest.raises(McpAuthorizationError):
            async with open_context():
                pytest.fail("Subject mismatch yielded")
    assert contexts.principals == []


@pytest.mark.parametrize("revision", [True, "7", -1, None])
async def test_fresh_claim_revision_must_be_a_nonnegative_integer(
    monkeypatch: pytest.MonkeyPatch, revision: object
) -> None:
    token = verified()
    token.claims["user_session_revision"] = revision
    provider, contexts = Provider(token), Contexts()
    async with httpx.AsyncClient() as http:
        open_context = factory(monkeypatch, provider, contexts, http, incoming=verified())
        with pytest.raises(ValidationError):
            async with open_context():
                pytest.fail("Invalid fresh claims yielded")
    assert contexts.principals == []


@pytest.mark.parametrize("where", ["verification", "body"])
async def test_cancellation_propagates_and_closes_opened_application_context(
    monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    provider, contexts = Provider(verified(), cancel=where == "verification"), Contexts()
    async with httpx.AsyncClient() as http:
        open_context = factory(monkeypatch, provider, contexts, http, incoming=verified())
        with pytest.raises(asyncio.CancelledError):
            async with open_context():
                raise asyncio.CancelledError
    assert provider.calls == [_OPAQUE]
    assert len(contexts.principals) == contexts.closed == (where == "body")
