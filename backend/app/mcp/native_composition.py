from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass

from google.cloud import tasks_v2
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings, parse_fernet_keys
from app.maintenance import MaintenanceModeMiddleware
from app.mcp.native_admission import PostgresRegistrationAdmission
from app.mcp.native_core import NativeOAuthCore
from app.mcp.native_provider import NativeWikiOAuthProvider
from app.mcp.native_request_context import require_native_oauth_request
from app.mcp.native_runtime import NativeMcpRuntime, create_native_runtime
from app.mcp.native_state import NativeStateStore
from app.mcp.protection import OAuthStateProtector
from app.mcp.redirect_policy import McpRedirectPolicy
from app.mcp.storage import PostgresOAuthStorage
from app.request_id import RequestIDMiddleware
from app.security_headers import SecurityHeadersMiddleware
from app.tasks.queue import CloudTasksEnqueuer


NATIVE_PATHS = frozenset(
    {
        "/authorize",
        "/token",
        "/register",
        "/revoke",
        "/mcp",
        "/mcp/",
        "/.well-known/oauth-authorization-server",
        "/.well-known/oauth-protected-resource/mcp",
    }
)


@dataclass(frozen=True, slots=True)
class NativeComposition:
    core: NativeOAuthCore
    runtime: NativeMcpRuntime


type NativeCompositionFactory = Callable[
    [Settings, httpx.AsyncClient, async_sessionmaker[AsyncSession]],
    AbstractAsyncContextManager[NativeComposition],
]


@dataclass(slots=True)
class NativeCompositionSlot:
    value: NativeComposition | None = None


@asynccontextmanager
async def native_composition(
    app_settings: Settings,
    http: httpx.AsyncClient,
    sessions: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[NativeComposition]:
    issuer = app_settings.MCP_NATIVE_ISSUER
    ring = app_settings.MCP_NATIVE_FERNET_KEYS
    lock_key = app_settings.MCP_NATIVE_LOCK_KEY
    if not app_settings.MCP_NATIVE_ENABLED or issuer is None or ring is None or lock_key is None:
        msg = "Native MCP is not configured"
        raise ValueError(msg)
    protector = OAuthStateProtector(
        tuple(parse_fernet_keys(ring.get_secret_value())),
        lock_key=parse_fernet_keys(lock_key.get_secret_value())[0],
    )
    core = NativeOAuthCore(
        PostgresOAuthStorage(sessions, protector),
        NativeStateStore(protector),
        f"{str(issuer).rstrip('/')}/mcp",
        issuer=str(issuer),
    )
    provider = NativeWikiOAuthProvider(
        core=core,
        base_url=str(issuer),
        consent_url=f"{app_settings.frontend_origin}/mcp/consent",
        redirects=McpRedirectPolicy(
            app_settings.MCP_NATIVE_LOOPBACK_PATHS, app_settings.MCP_NATIVE_HTTPS_CALLBACKS
        ),
        request_guard=require_native_oauth_request,
        registration_admission=PostgresRegistrationAdmission(sessions).admit,
        issuer_responses_enabled=True,
    )
    async with tasks_v2.CloudTasksAsyncClient() as client:
        enqueuer = CloudTasksEnqueuer(
            project=_required(app_settings.GOOGLE_CLOUD_PROJECT),
            location=_required(app_settings.CLOUD_TASKS_LOCATION),
            queue=_required(app_settings.CLOUD_TASKS_QUEUE),
            target_url=_required(app_settings.CLOUD_TASKS_TARGET_URL),
            invoker_service_account=_required(app_settings.CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT),
            document_transform_queue=app_settings.DOCUMENT_TRANSFORM_QUEUE,
            document_transform_target_url=app_settings.DOCUMENT_TRANSFORM_TARGET_URL,
            client=client,
        )
        yield NativeComposition(
            core,
            create_native_runtime(
                provider,
                sessions=sessions,
                http=http,
                enqueuer=enqueuer,
                frontend_origin=app_settings.frontend_origin,
            ),
        )


def _required(value: str | None) -> str:
    if value is None:
        msg = "Native MCP requires configured task infrastructure"
        raise ValueError(msg)
    return value


class NativeDispatchMiddleware:
    def __init__(self, app: ASGIApp, *, slot: NativeCompositionSlot, maintenance: bool) -> None:
        self._rest = app
        self._slot = slot
        self._native = SecurityHeadersMiddleware(
            MaintenanceModeMiddleware(
                RequestIDMiddleware(self._dispatch, static_routes=NATIVE_PATHS), enabled=maintenance
            ),
            enable_hsts=True,
        )

    async def _dispatch(self, scope: Scope, receive: Receive, send: Send) -> None:
        composition = self._slot.value
        if composition is None:
            await JSONResponse({"detail": "Service unavailable"}, status_code=503)(
                scope, receive, send
            )
            return
        await composition.runtime.app(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") in NATIVE_PATHS:
            await self._native(scope, receive, send)
            return
        await self._rest(scope, receive, send)
