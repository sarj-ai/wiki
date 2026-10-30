from dataclasses import dataclass
from urllib.parse import urlsplit

from fastmcp.server.http import StarletteWithLifespan
import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import ASGIApp

from app.mcp.native_context import native_context_factory
from app.mcp.native_http_guard import NativeOAuthGuardSettings, NativeOAuthHTTPGuard
from app.mcp.native_provider import NativeWikiOAuthProvider
from app.mcp.native_schemas import NativeOAuthError
from app.mcp.server import create_mcp_server
from app.request_body import MAX_MCP_REQUEST_BODY_BYTES, RequestBodyLimitMiddleware
from app.tasks.queue import TaskEnqueuer


@dataclass(frozen=True, slots=True)
class NativeMcpRuntime:
    app: ASGIApp
    http_app: StarletteWithLifespan


def create_native_runtime(
    provider: NativeWikiOAuthProvider,
    *,
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    enqueuer: TaskEnqueuer,
    frontend_origin: str,
) -> NativeMcpRuntime:
    # Explicit factory only: production mounting and lifespan ownership are separate gates.
    resource = urlsplit(provider.core.resource)
    issuer = urlsplit(provider.core.issuer)
    frontend = urlsplit(frontend_origin)
    if (
        resource.scheme != "https"
        or resource.netloc != issuer.netloc
        or resource.path != "/mcp"
        or resource.query
        or resource.fragment
    ):
        raise NativeOAuthError
    if (
        frontend.scheme != "https"
        or frontend_origin != f"{frontend.scheme}://{frontend.netloc}"
        or frontend.username is not None
    ):
        raise NativeOAuthError
    server = create_mcp_server(
        auth=provider,
        context_factory=native_context_factory(provider, sessions=sessions, http=http),
        enqueuer=enqueuer,
    )
    http_app = server.http_app(
        path="/mcp",
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=[resource.netloc],
        allowed_origins=[frontend_origin],
    )
    guarded = NativeOAuthHTTPGuard(
        http_app,
        NativeOAuthGuardSettings(
            resource=provider.core.resource,
            issuer=provider.core.issuer,
            redirect_policy=provider.redirects,
        ),
    )
    return NativeMcpRuntime(
        app=RequestBodyLimitMiddleware(
            guarded,
            max_bytes=65_536,
            path_limits={"/mcp": MAX_MCP_REQUEST_BODY_BYTES},
        ),
        http_app=http_app,
    )
