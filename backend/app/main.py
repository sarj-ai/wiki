import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
import httpx
from loguru import logger
from sqlalchemy import text

from app.aliases.errors import AliasError
from app.aliases.router import router as aliases_router
from app.auth.router import router as auth_router
from app.bootstrap.router import router as bootstrap_router
from app.config import Settings, settings
from app.csrf import CookieOriginMiddleware
from app.database import async_session, close_connector
from app.document_transform.qa_router import (
    issuer_router as document_transform_qa_issuer_router,
    workload_router as document_transform_qa_workload_router,
)
from app.document_transform.router import router as document_transform_router
from app.drive.errors import DrivePermissionsError
from app.drive.search_router import router as drive_search_router
from app.extras.router import router as extras_router
from app.files.errors import FilesError
from app.files.router import router as files_router
from app.logging_config import configure_logging
from app.maintenance import MaintenanceModeMiddleware
from app.mcp.native_composition import (
    NativeCompositionFactory,
    NativeCompositionSlot,
    NativeDispatchMiddleware,
    native_composition,
)
from app.mcp.native_consent_router import NativeConsentSettings, create_native_consent_router
from app.mcp.native_core import NativeOAuthCore
from app.mcp.redirect_policy import McpRedirectPolicy
from app.request_body import (
    MAX_MCP_REQUEST_BODY_BYTES,
    MAX_REQUEST_BODY_BYTES,
    RequestBodyLimitMiddleware,
)
from app.request_id import RequestIDMiddleware
from app.security_headers import SecurityHeadersMiddleware
from app.sidebar.router import router as sidebar_router
from app.smart_create.router import router as smart_create_router
from app.tasks.errors import TaskError
from app.tasks.internal_router import router as internal_tasks_router
from app.tasks.router import router as tasks_router
from app.templates.router import router as templates_router


async def global_exception_handler(
    _request: Request,
    _exc: Exception,
) -> JSONResponse:
    logger.error("unhandled request exception")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


async def files_error_handler(
    _request: Request,
    exc: FilesError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "detail": exc.public_detail},
    )


async def drive_permissions_error_handler(
    _request: Request,
    exc: DrivePermissionsError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "detail": str(exc)},
    )


async def alias_error_handler(
    _request: Request,
    exc: AliasError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "detail": str(exc)},
    )


async def task_error_handler(
    _request: Request,
    exc: TaskError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "detail": str(exc)},
    )


async def _health_check(app_settings: Settings) -> dict[str, str]:
    result: dict[str, str] = {"revision": app_settings.RELEASE_SHA, "status": "ok"}
    database_result = await asyncio.gather(
        asyncio.wait_for(_probe_database(), timeout=3.0), return_exceptions=True
    )
    database_outcome = database_result[0]
    if not isinstance(database_outcome, BaseException):
        result["database"] = "ok"
    elif not isinstance(database_outcome, Exception):
        raise database_outcome
    else:
        result["status"] = "degraded"
        result["database"] = "unavailable"
    return result


async def _probe_database() -> None:
    async with async_session() as db:
        await db.execute(text("SELECT 1"))


async def health_check() -> dict[str, str]:
    return await _health_check(settings)


def create_app(
    app_settings: Settings, *, native_factory: NativeCompositionFactory = native_composition
) -> FastAPI:
    native_slot = NativeCompositionSlot()

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncGenerator[None]:
        configure_logging(json=app_settings.LOG_JSON, level=app_settings.LOG_LEVEL)
        try:
            async with AsyncExitStack() as stack:
                http_client = await stack.enter_async_context(httpx.AsyncClient(timeout=10.0))
                instance.state.http_client = http_client
                if app_settings.MCP_NATIVE_ENABLED:
                    composition = await stack.enter_async_context(
                        native_factory(app_settings, http_client, async_session)
                    )
                    await stack.enter_async_context(
                        composition.runtime.http_app.lifespan(composition.runtime.http_app)
                    )
                    native_slot.value = composition
                try:
                    yield
                finally:
                    native_slot.value = None
        finally:
            await close_connector()

    docs_url = "/docs" if app_settings.API_DOCS_ENABLED else None
    application = FastAPI(
        title="Sarj Wiki API",
        version=version("sarj-docs-backend"),
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url="/redoc" if app_settings.API_DOCS_ENABLED else None,
        openapi_url="/openapi.json" if app_settings.API_DOCS_ENABLED else None,
    )
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=MAX_REQUEST_BODY_BYTES,
        path_limits={"/mcp": MAX_MCP_REQUEST_BODY_BYTES},
    )
    application.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=4)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
            "X-MCP-CSRF",
        ],
        expose_headers=[
            "Retry-After",
            "Server-Timing",
            "Idempotency-Replayed",
            "X-Request-ID",
        ],
    )
    application.add_middleware(
        CookieOriginMiddleware,
        allowed_origin=app_settings.frontend_origin,
    )
    application.add_middleware(RequestIDMiddleware, static_routes=frozenset())
    application.add_middleware(
        MaintenanceModeMiddleware,
        enabled=app_settings.MAINTENANCE_MODE,
    )
    application.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=app_settings.FRONTEND_URL.scheme == "https",
    )

    application.exception_handler(Exception)(global_exception_handler)
    application.exception_handler(FilesError)(files_error_handler)
    application.exception_handler(DrivePermissionsError)(drive_permissions_error_handler)
    application.exception_handler(AliasError)(alias_error_handler)
    application.exception_handler(TaskError)(task_error_handler)

    application.include_router(auth_router)
    application.include_router(bootstrap_router)
    application.include_router(sidebar_router)
    application.include_router(files_router)
    application.include_router(extras_router)
    application.include_router(drive_search_router)
    application.include_router(aliases_router)
    application.include_router(templates_router)
    application.include_router(smart_create_router)
    application.include_router(document_transform_router)
    application.include_router(document_transform_qa_issuer_router)
    application.include_router(document_transform_qa_workload_router)
    application.include_router(tasks_router)
    application.include_router(internal_tasks_router)

    if app_settings.MCP_NATIVE_ENABLED:

        async def native_core_dependency() -> NativeOAuthCore:
            if native_slot.value is None:
                raise HTTPException(status_code=503)
            return native_slot.value.core

        application.include_router(
            create_native_consent_router(
                settings=NativeConsentSettings(
                    frontend_origin=app_settings.frontend_origin,
                    issuer=str(app_settings.MCP_NATIVE_ISSUER),
                    redirect_policy=McpRedirectPolicy(
                        app_settings.MCP_NATIVE_LOOPBACK_PATHS,
                        app_settings.MCP_NATIVE_HTTPS_CALLBACKS,
                    ),
                ),
                core_dependency=native_core_dependency,
            )
        )
        application.add_middleware(
            NativeDispatchMiddleware, slot=native_slot, maintenance=app_settings.MAINTENANCE_MODE
        )

    if app_settings is settings:
        application.get("/health", include_in_schema=False)(health_check)
    else:

        async def configured_health_check() -> dict[str, str]:
            return await _health_check(app_settings)

        application.get("/health", include_in_schema=False)(configured_health_check)

    return application


app = create_app(settings)
