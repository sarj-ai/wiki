import asyncio
from base64 import urlsafe_b64encode
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
from types import SimpleNamespace
from typing import Any, override
from urllib.parse import parse_qs, urlsplit
import uuid

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastmcp.server.http import StarletteWithLifespan
import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import Table
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.auth.service import AuthService, AuthSession
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.identifiers import GoogleUserId, TaskId, UserId
from app.mcp.native_admission import PostgresRegistrationAdmission
from app.mcp.native_consent_router import NativeConsentSettings, create_native_consent_router
from app.mcp.native_core import NativeOAuthCore
from app.mcp.native_provider import NativeWikiOAuthProvider
from app.mcp.native_request_context import require_native_oauth_request
from app.mcp.native_runtime import create_native_runtime
from app.mcp.native_state import NativeStateStore
from app.mcp.protection import OAuthStateProtector
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.storage import PostgresOAuthStorage
from app.models.mcp_grant import McpGrant
from app.models.mcp_native_state import McpNativeState
from app.models.mcp_registration_admission import McpRegistrationAdmission
from app.models.user import User
from app.tasks.queue import TaskEnqueuer


_ISSUER = "https://api.runtime.test/"
_RESOURCE = "https://api.runtime.test/mcp"
_FRONTEND = "https://wiki.runtime.test"
_CALLBACK = "http://127.0.0.1:4321/callback"
_VERIFIER = "v" * 43
_CHALLENGE = urlsafe_b64encode(hashlib.sha256(_VERIFIER.encode()).digest()).decode().rstrip("=")
_CANARY = "synthetic-private-runtime-canary"


class QASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_MCP_QA_")
    database_url: str | None = None


class BrowserAuth(AuthService):
    def __init__(self, user: User) -> None:
        self.user = user

    @override
    async def resolve_current_user(self, jwt_token: str) -> User:
        assert jwt_token == "synthetic-wiki-session"
        return self.user

    @override
    async def exchange_code_for_session(
        self, code: str, state: str, expected_state: str | None
    ) -> AuthSession:
        pytest.fail("Unexpected upstream Google login")

    @override
    async def refresh_session(self, refresh_token: str) -> AuthSession:
        pytest.fail("Unexpected browser refresh")

    @override
    async def revoke_session(self, access_token: str | None) -> None:
        pytest.fail("Unexpected global logout")


class NoQueue(TaskEnqueuer):
    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        pytest.fail("Unexpected task submission")


@dataclass
class RuntimeLab:
    browser: httpx.AsyncClient
    client: httpx.AsyncClient
    provider: NativeWikiOAuthProvider
    prepared: list[UserId]

    async def register(self) -> str:
        response = await self.registration_response()
        assert response.status_code == 201, response.text
        return str(response.json()["client_id"])

    async def registration_response(self) -> httpx.Response:
        return await self.client.post(
            "/register",
            json={
                "redirect_uris": [_CALLBACK],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "wiki:read wiki:transform",
                "client_name": "Synthetic Runtime Client",
            },
        )

    async def authorize(self, client_id: str) -> str:
        response = await self.browser.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": _CALLBACK,
                "response_type": "code",
                "code_challenge": _CHALLENGE,
                "code_challenge_method": "S256",
                "resource": _RESOURCE,
                "scope": "wiki:read wiki:transform",
                "state": "synthetic-client-state",
            },
        )
        assert response.status_code == 302, response.text
        return parse_qs(urlsplit(response.headers["location"]).query)["intent"][0]

    async def consent(self, intent: str, *, approve: bool) -> dict[str, list[str]]:
        path = f"/integrations/mcp/consent/{intent}"
        headers = {"Origin": _FRONTEND}
        view = await self.browser.get(path, headers=headers)
        assert view.status_code == 200, view.text
        assert view.json()["account_email"] == "alice@runtime.test"
        response = await self.browser.post(
            path,
            headers=headers,
            json={"approve": approve, "transform_approved": False, "csrf": view.json()["csrf"]},
        )
        assert response.status_code == 200, response.text
        result = parse_qs(urlsplit(response.json()["redirect_uri"]).query)
        assert result["iss"] == [_ISSUER]
        assert result["state"] == ["synthetic-client-state"]
        return result

    async def exchange(self, client_id: str, code: str, **changes: str) -> httpx.Response:
        return await self.client.post(
            "/token",
            data={
                "client_id": client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _CALLBACK,
                "code_verifier": _VERIFIER,
                "resource": _RESOURCE,
                **changes,
            },
        )

    async def rpc(self, token: str, method: str, params: dict[str, object]) -> httpx.Response:
        return await self.client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )


@asynccontextmanager
async def running_lifespan(app: StarletteWithLifespan) -> AsyncGenerator[None]:
    started, stopped = asyncio.Event(), asyncio.Event()

    async def lifespan() -> None:
        # AnyIO scopes must enter/exit in one task; pytest fixture yield
        # resumes teardown in another task under pytest-asyncio.
        async with app.router.lifespan_context(app):
            started.set()
            await stopped.wait()

    lifecycle = asyncio.create_task(lifespan())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        yield
    finally:
        stopped.set()
        await lifecycle


@pytest.fixture(params=[False, True], ids=["issuer-default-off", "issuer-composed-on"])
async def runtime(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> AsyncGenerator[RuntimeLab]:
    dsn = QASettings().database_url
    if dsn is None:
        pytest.skip("Requires disposable native MCP runtime PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (
            (url.database or "").startswith("wiki_mcp_qa") or url.database == "wiki_polish29_qa"
        )
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only loopback PostgreSQL named wiki_mcp_qa* or wiki_polish29_qa is allowed")
    schema = "native_runtime_" + uuid.uuid7().hex
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    protector = OAuthStateProtector(
        (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
    )
    core = NativeOAuthCore(
        PostgresOAuthStorage(sessions, protector),
        NativeStateStore(protector),
        _RESOURCE,
        issuer=_ISSUER,
    )
    policy = McpRedirectPolicy(loopback_paths=("/callback",))
    provider = NativeWikiOAuthProvider(
        core=core,
        base_url=_ISSUER,
        consent_url=f"{_FRONTEND}/mcp/consent",
        redirects=policy,
        request_guard=require_native_oauth_request,
        registration_admission=PostgresRegistrationAdmission(sessions).admit,
        issuer_responses_enabled=request.param,
    )
    prepared: list[UserId] = []

    async def prepare(_manager: object, user: User) -> Any:
        # Only the existing Google credential/network boundary is synthetic.
        # OAuth state, grants, HTTP handlers, MCP auth and fresh user reads are real.
        prepared.append(user.id)
        return SimpleNamespace(user=user, refresh_handler=None)

    monkeypatch.setattr("app.mcp.context.GoogleTokenManager.prepare", prepare)
    try:
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
            for model in (User, McpGrant, McpNativeState, McpRegistrationAdmission):
                table = model.__table__
                assert isinstance(table, Table)
                await connection.run_sync(table.create)
        user = User(
            id=UserId(uuid.uuid7()),
            google_id=GoogleUserId("runtime-alice"),
            email="alice@runtime.test",
            encrypted_refresh_token="synthetic-encrypted-placeholder",
        )
        async with sessions.begin() as db:
            db.add(user)
        auth = BrowserAuth(user)

        def get_core() -> NativeOAuthCore:
            return core

        def get_auth() -> AuthService:
            return auth

        async with httpx.AsyncClient() as upstream:
            composed = create_native_runtime(
                provider,
                sessions=sessions,
                http=upstream,
                enqueuer=NoQueue(),
                frontend_origin=_FRONTEND,
            )
            edge = FastAPI()
            edge.include_router(
                create_native_consent_router(
                    settings=NativeConsentSettings(_FRONTEND, policy, _ISSUER),
                    core_dependency=get_core,
                    auth_dependency=get_auth,
                )
            )
            edge.mount("/", composed.app)
            async with (
                running_lifespan(composed.http_app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=edge), base_url=_ISSUER
                ) as browser,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=edge), base_url=_ISSUER
                ) as client,
            ):
                browser.cookies.set(
                    "access_token", "synthetic-wiki-session", domain="api.runtime.test", path="/"
                )
                yield RuntimeLab(browser, client, provider, prepared)
    finally:
        async with engine.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await engine.dispose()


async def test_real_http_pg_oauth_to_mcp_and_revocation(
    runtime: RuntimeLab, caplog: pytest.LogCaptureFixture
) -> None:
    metadata = await runtime.client.get("/.well-known/oauth-authorization-server")
    assert (
        metadata.json()["authorization_response_iss_parameter_supported"]
        is runtime.provider.issuer_responses_enabled
    )
    assert metadata.json()["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata.json()["client_id_metadata_document_supported"] is False
    client_id = await runtime.register()
    intent = await runtime.authorize(client_id)
    approved = await runtime.consent(intent, approve=True)
    code = approved["code"][0]
    wrong_resource = await runtime.exchange(client_id, code, resource="https://wrong.test/mcp")
    assert wrong_resource.status_code == 400
    wrong_pkce = await runtime.exchange(client_id, code, code_verifier="w" * 43)
    assert wrong_pkce.status_code == 401
    response = await runtime.exchange(client_id, code)
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "wiki:read"
    token = str(response.json()["access_token"])
    initialized = await runtime.rpc(
        token,
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "synthetic-runtime-test", "version": "1"},
        },
    )
    assert initialized.status_code == 200, initialized.text
    tools = await runtime.rpc(token, "tools/list", {})
    assert {tool["name"] for tool in tools.json()["result"]["tools"]} == {
        "wiki_connection_status",
        "wiki_search",
        "wiki_get_document",
        "wiki_polish_document",
        "wiki_import_markdown",
        "wiki_get_polish_admission",
        "wiki_get_task",
        "wiki_get_short_link",
    }
    for _ in range(2):
        connected = await runtime.rpc(
            token,
            "tools/call",
            {
                "name": "wiki_connection_status",
                "arguments": {"request": {}},
            },
        )
        assert connected.json()["result"]["structuredContent"]["capabilities"] == ["wiki:read"]
    assert len(runtime.prepared) == 2

    malformed = await runtime.rpc(
        token,
        "tools/call",
        {
            "name": "wiki_connection_status",
            "arguments": {"request": {"private": _CANARY}},
        },
    )
    assert malformed.json()["result"]["isError"] is True
    assert _CANARY not in malformed.text
    assert _CANARY not in caplog.text
    assert len(runtime.prepared) == 2
    revoked = await runtime.client.post("/revoke", data={"client_id": client_id, "token": token})
    assert revoked.status_code == 200, revoked.text
    denied = await runtime.rpc(
        token, "tools/call", {"name": "wiki_connection_status", "arguments": {"request": {}}}
    )
    assert denied.status_code == 401
    assert len(runtime.prepared) == 2


async def test_import_maximum_escaped_markdown_reaches_tool_validation(runtime: RuntimeLab) -> None:
    client_id = await runtime.register()
    approved = await runtime.consent(await runtime.authorize(client_id), approve=True)
    issued = await runtime.exchange(client_id, approved["code"][0])
    assert issued.status_code == 200
    token = str(issued.json()["access_token"])
    result = await runtime.rpc(
        token,
        "tools/call",
        {
            "name": "wiki_import_markdown",
            "arguments": {
                "request": {
                    "content": "\x01" * MAX_MARKDOWN_BYTES,
                    "name": "Report",
                    "destination_folder_id": "folder_123",
                    "mode": "format",
                    "idempotency_key": str(uuid.uuid7()),
                }
            },
        },
    )
    assert result.status_code == 200, result.text[:1000]
    assert result.json()["result"]["isError"] is True
    assert "authorization required" in result.text


async def test_registration_limit_preserves_refresh_read_and_revoke(runtime: RuntimeLab) -> None:
    client_id = await runtime.register()
    approved = await runtime.consent(await runtime.authorize(client_id), approve=True)
    issued = await runtime.exchange(client_id, approved["code"][0])
    assert issued.status_code == 200
    for _ in range(9):
        await runtime.register()
    rejected = await runtime.registration_response()
    assert rejected.status_code == 429
    assert 1 <= int(rejected.headers["retry-after"]) <= 60
    assert "no-store" in rejected.headers["cache-control"]
    refreshed = await runtime.client.post(
        "/token",
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": issued.json()["refresh_token"],
            "resource": _RESOURCE,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    token = str(refreshed.json()["access_token"])
    connected = await runtime.rpc(
        token, "tools/call", {"name": "wiki_connection_status", "arguments": {"request": {}}}
    )
    assert connected.json()["result"]["structuredContent"]["capabilities"] == ["wiki:read"]
    revoked = await runtime.client.post("/revoke", data={"client_id": client_id, "token": token})
    assert revoked.status_code == 200, revoked.text
    denied = await runtime.rpc(
        token, "tools/call", {"name": "wiki_connection_status", "arguments": {"request": {}}}
    )
    assert denied.status_code == 401
    assert (await runtime.registration_response()).status_code == 429


async def test_real_http_denial_and_sdk_error_have_matching_issuer(runtime: RuntimeLab) -> None:
    client_id = await runtime.register()
    denied = await runtime.consent(await runtime.authorize(client_id), approve=False)
    assert denied["error"] == ["access_denied"]
    response = await runtime.browser.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": _CALLBACK,
            "response_type": "code",
            "code_challenge": _CHALLENGE,
            "code_challenge_method": "S256",
            "resource": _RESOURCE,
            "scope": "wiki:transform",
            "state": "state",
        },
    )
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["iss"] == [_ISSUER]
    assert query["error"] == ["access_denied"]


async def test_runtime_rejects_hostile_transport_and_private_malformed_input(
    runtime: RuntimeLab,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for headers in (
        {"Host": "attacker.test"},
        {"Origin": "https://attacker.test"},
        {"Origin": "null"},
    ):
        response = await runtime.client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert response.status_code in {400, 403, 421}
    response = await runtime.client.post(
        "/token",
        content=f"code={_CANARY}&code=duplicate",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 400
    assert _CANARY not in response.text
    assert _CANARY not in caplog.text
