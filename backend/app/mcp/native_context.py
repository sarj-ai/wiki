from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastmcp.server.dependencies import get_access_token
import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.identifiers import UserId
from app.mcp.context import (
    McpApplicationContext,
    McpAuthorizationError,
    ValidatedMcpPrincipal,
    open_mcp_context,
)
from app.mcp.native_provider import NativeWikiOAuthProvider
from app.mcp.schemas import McpCapability
from app.mcp.server import ContextFactory


class _VerifiedClaims(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    user_id: UserId
    user_session_revision: int = Field(strict=True, ge=0)


def native_context_factory(
    provider: NativeWikiOAuthProvider,
    *,
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> ContextFactory:
    @asynccontextmanager
    async def context() -> AsyncGenerator[McpApplicationContext]:
        incoming = get_access_token()
        if incoming is None:
            raise McpAuthorizationError
        # Revalidate opaque credentials and the live grant for every tool call.
        # A Streamable HTTP session must not retain a principal after disconnect.
        if (verified := await provider.load_access_token(incoming.token)) is None:
            raise McpAuthorizationError
        claims = _VerifiedClaims.model_validate(verified.claims)
        if verified.subject != str(claims.user_id):
            raise McpAuthorizationError
        principal = ValidatedMcpPrincipal(
            user_id=UserId(claims.user_id),
            user_session_revision=claims.user_session_revision,
            capabilities=frozenset(McpCapability(scope) for scope in verified.scopes),
        )
        async with open_mcp_context(principal, sessionmaker=sessions, http=http) as opened:
            yield opened

    return context
