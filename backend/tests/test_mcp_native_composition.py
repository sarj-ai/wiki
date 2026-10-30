from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet
import httpx
from pydantic import ValidationError
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.identifiers import TaskId
from app.main import create_app
from app.mcp.native_admission import PostgresRegistrationAdmission
from app.mcp.native_composition import NATIVE_PATHS, NativeComposition
from app.mcp.native_core import NativeOAuthCore
from app.mcp.native_provider import NativeWikiOAuthProvider
from app.mcp.native_request_context import require_native_oauth_request
from app.mcp.native_runtime import create_native_runtime
from app.mcp.native_state import NativeStateStore
from app.mcp.protection import OAuthStateProtector
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.storage import PostgresOAuthStorage
from app.tasks.queue import TaskEnqueuer


ORIGIN = "https://wiki.example.test"
ISSUER = "https://api.example.test/"


def configured(**updates: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
        "SECRET_KEY": "s" * 32,
        "GOOGLE_CLIENT_ID": "client",
        "GOOGLE_CLIENT_SECRET": "secret",
        "FERNET_KEY": Fernet.generate_key().decode(),
        "CACHE_FERNET_KEY": None,
        "FRONTEND_URL": ORIGIN,
        "GOOGLE_REDIRECT_URI": f"{ISSUER}auth/callback",
        "COOKIE_DOMAIN": "example.test",
        "MCP_NATIVE_ENABLED": True,
        "MCP_NATIVE_ISSUER": ISSUER,
        "MCP_NATIVE_FERNET_KEYS": Fernet.generate_key().decode(),
        "MCP_NATIVE_LOCK_KEY": Fernet.generate_key().decode(),
        "MCP_NATIVE_LOOPBACK_PATHS": ("/callback",),
        "GOOGLE_CLOUD_PROJECT": "project",
        "CLOUD_TASKS_QUEUE": "queue",
        "CLOUD_TASKS_LOCATION": "region",
        "CLOUD_TASKS_TARGET_URL": f"{ISSUER}internal/tasks/smart-create",
        "CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT": "tasks@example.test",
        "DOCUMENT_TRANSFORM_QUEUE": "transform",
        "DOCUMENT_TRANSFORM_TARGET_URL": f"{ISSUER}internal/tasks/document-transform",
    }
    return Settings.model_validate(values | updates)


class NoEnqueue(TaskEnqueuer):
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        pytest.fail("No task admission expected")

    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        pytest.fail("No task admission expected")


class Factory:
    def __init__(self) -> None:
        self.calls = 0
        self.stopped = 0
        self.http: httpx.AsyncClient | None = None
        self.paths: set[str] = set()
        self.fail_start = False

    @asynccontextmanager
    async def __call__(
        self,
        app_settings: Settings,
        http: httpx.AsyncClient,
        sessions: async_sessionmaker[AsyncSession],
    ) -> AsyncGenerator[NativeComposition]:
        self.calls += 1
        self.http = http
        if self.fail_start:
            msg = "Synthetic startup failure"
            raise RuntimeError(msg)
        protector = OAuthStateProtector(
            (Fernet.generate_key().decode(),), lock_key=Fernet.generate_key().decode()
        )
        core = NativeOAuthCore(
            PostgresOAuthStorage(sessions, protector),
            NativeStateStore(protector),
            f"{ISSUER}mcp",
            issuer=ISSUER,
        )
        provider = NativeWikiOAuthProvider(
            core=core,
            base_url=ISSUER,
            consent_url=f"{ORIGIN}/mcp/consent",
            redirects=McpRedirectPolicy(loopback_paths=("/callback",)),
            request_guard=require_native_oauth_request,
            registration_admission=PostgresRegistrationAdmission(sessions).admit,
            issuer_responses_enabled=True,
        )
        runtime = create_native_runtime(
            provider,
            sessions=sessions,
            http=http,
            enqueuer=NoEnqueue(),
            frontend_origin=app_settings.frontend_origin,
        )
        self.paths = {route.path for route in provider.get_routes("/mcp")} | {"/mcp"}
        try:
            yield NativeComposition(core, runtime)
        finally:
            assert not http.is_closed
            self.stopped += 1


def test_native_config_defaults_off_without_keys() -> None:
    config = configured(
        MCP_NATIVE_ENABLED=False,
        MCP_NATIVE_ISSUER=None,
        MCP_NATIVE_FERNET_KEYS=None,
        MCP_NATIVE_LOCK_KEY=None,
        MCP_NATIVE_LOOPBACK_PATHS=(),
    )
    assert config.MCP_NATIVE_ENABLED is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("MCP_NATIVE_ISSUER", None),
        ("MCP_NATIVE_ISSUER", "http://api.example.test/"),
        ("MCP_NATIVE_ISSUER", "https://api.example.test/prefix"),
        ("MCP_NATIVE_ISSUER", "https://evil.example.test/"),
        ("MCP_NATIVE_FERNET_KEYS", None),
        ("MCP_NATIVE_LOCK_KEY", None),
        ("MCP_NATIVE_LOOPBACK_PATHS", ()),
        ("MCP_NATIVE_LOOPBACK_PATHS", ("/*",)),
        ("FRONTEND_URL", "https://wiki.example.test/path"),
        ("COOKIE_DOMAIN", None),
        ("GOOGLE_CLOUD_PROJECT", None),
    ],
    ids=[
        "missing-issuer",
        "http",
        "prefix",
        "wrong-api",
        "missing-ring",
        "missing-lock",
        "no-callback",
        "wildcard",
        "frontend-path",
        "no-site",
        "no-project",
    ],
)
def test_native_config_rejects_incomplete_or_unsafe_activation(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        configured(**{field: value})


def test_native_keys_are_distinct_and_redacted() -> None:
    key = Fernet.generate_key().decode()
    with pytest.raises(ValidationError, match="distinct"):
        configured(MCP_NATIVE_FERNET_KEYS=key, MCP_NATIVE_LOCK_KEY=key)
    with pytest.raises(ValidationError, match="distinct"):
        configured(MCP_NATIVE_FERNET_KEYS=key, FERNET_KEY=key)
    config = configured(MCP_NATIVE_FERNET_KEYS=key)
    assert key not in repr(config)


async def test_disabled_mount_never_constructs_native_runtime() -> None:
    factory = Factory()
    app = create_app(configured(MCP_NATIVE_ENABLED=False), native_factory=factory)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as http,
    ):
        for path in (*NATIVE_PATHS, "/integrations/mcp/connections"):
            assert (await http.get(path)).status_code == 404
    assert factory.calls == 0


async def test_enabled_actual_inventory_lifespan_and_rest_security_are_separate() -> None:
    factory = Factory()
    app = create_app(configured(), native_factory=factory)
    async with app.router.lifespan_context(app):
        assert factory.calls == 1
        assert factory.http is app.state.http_client
        assert factory.paths == NATIVE_PATHS - {"/mcp/"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=ISSUER
        ) as http:
            metadata = await http.get("/.well-known/oauth-authorization-server")
            assert metadata.status_code == 200
            assert metadata.json()["issuer"] == ISSUER
            assert metadata.json()["authorization_response_iss_parameter_supported"] is True
            assert metadata.headers["referrer-policy"] == "no-referrer"
            assert "x-request-id" in metadata.headers
            invalid = await http.post("/token", data={"grant_type": "refresh_token"})
            assert invalid.status_code == 400
            assert invalid.json()["error"] == "invalid_request"
            bearer = await http.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            )
            assert bearer.status_code == 401
            denied = await http.post("/aliases", headers={"Cookie": "access_token=session"})
            assert denied.status_code == 403
            preflight = await http.options(
                "/integrations/mcp/connections/id",
                headers={
                    "Origin": ORIGIN,
                    "Access-Control-Request-Method": "DELETE",
                    "Access-Control-Request-Headers": "X-MCP-CSRF",
                },
            )
            assert preflight.status_code == 200
            assert preflight.headers["access-control-allow-origin"] == ORIGIN
    assert factory.stopped == 1
    assert factory.http is not None
    assert factory.http.is_closed


async def test_native_maintenance_preserves_shared_protection() -> None:
    app = create_app(configured(MAINTENANCE_MODE=True), native_factory=Factory())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as http,
    ):
        response = await http.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_failed_startup_closes_http_and_never_exposes_native_runtime() -> None:
    factory = Factory()
    factory.fail_start = True
    app = create_app(configured(), native_factory=factory)
    with pytest.raises(RuntimeError, match="Synthetic startup failure"):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup must fail closed")
    assert factory.http is not None
    assert factory.http.is_closed
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ISSUER) as http:
        response = await http.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 503


async def test_successive_lifespans_create_one_runtime_each() -> None:
    factory = Factory()
    app = create_app(configured(), native_factory=factory)
    for _ in range(2):
        async with app.router.lifespan_context(app):
            assert factory.http is not None
            assert not factory.http.is_closed
    assert factory.calls == 2
    assert factory.stopped == 2
