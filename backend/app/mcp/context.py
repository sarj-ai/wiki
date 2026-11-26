from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.service import AliasService, PostgresAliasService
from app.auth.google_tokens import GoogleReauthenticationRequiredError, GoogleTokenManager
from app.drive.service import DriveService, GoogleDriveService
from app.files.service import FilesService, GoogleFilesService
from app.identifiers import UserId
from app.mcp.schemas import McpCapability
from app.models.user import User
from app.performance import provider_retry_budget
from app.tasks.service import PostgresTaskService, TaskService


class McpAuthorizationError(Exception):
    def __init__(self) -> None:
        super().__init__("Wiki integration authorization required")


@dataclass(frozen=True, slots=True)
class ValidatedMcpPrincipal:
    user_id: UserId
    user_session_revision: int
    capabilities: frozenset[McpCapability]

    def require(self, capability: McpCapability) -> None:
        if capability not in self.capabilities:
            raise McpAuthorizationError


@dataclass(frozen=True, slots=True)
class McpApplicationContext:
    principal: ValidatedMcpPrincipal
    drive: DriveService
    files: FilesService
    tasks: TaskService
    aliases: AliasService


type ContextFactory = Callable[[], AbstractAsyncContextManager[McpApplicationContext]]
DEFAULT_TOOL_TIMEOUT = timedelta(seconds=30)
_PROVIDER_RETRY_BUDGET = timedelta(seconds=20)


@asynccontextmanager
async def authorized_mcp_context(
    factory: ContextFactory,
    capability: McpCapability = McpCapability.READ,
    *,
    request_timeout: timedelta = DEFAULT_TOOL_TIMEOUT,
) -> AsyncGenerator[McpApplicationContext]:
    with provider_retry_budget(_PROVIDER_RETRY_BUDGET):
        async with asyncio.timeout(request_timeout.total_seconds()), factory() as context:
            context.principal.require(McpCapability.READ)
            context.principal.require(capability)
            yield context


@asynccontextmanager
async def open_mcp_context(
    principal: ValidatedMcpPrincipal,
    *,
    sessionmaker: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    http: httpx.AsyncClient,
) -> AsyncGenerator[McpApplicationContext]:
    principal.require(McpCapability.READ)
    async with sessionmaker() as db:
        if (user := await db.get(User, principal.user_id)) is None:
            raise McpAuthorizationError
        current_revision = user.token_version
        if current_revision != principal.user_session_revision:
            raise McpAuthorizationError
        if user.encrypted_refresh_token is None:
            raise GoogleReauthenticationRequiredError
        prepared = await GoogleTokenManager(db, http).prepare(user)
        await db.commit()
        drive = GoogleDriveService(
            prepared.user, cache_service=None, refresh_handler=prepared.refresh_handler
        )
        files = GoogleFilesService(user=prepared.user, drive=drive)
        yield McpApplicationContext(
            principal=principal,
            drive=drive,
            files=files,
            tasks=PostgresTaskService(db=db),
            aliases=PostgresAliasService(db=db, files=files, drive=drive),
        )
