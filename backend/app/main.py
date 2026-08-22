from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI, Request
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
from app.drive.errors import DrivePermissionsError
from app.drive.search_router import router as drive_search_router
from app.extras.router import router as extras_router
from app.files.errors import FilesError
from app.files.router import router as files_router
from app.logging_config import configure_logging
from app.request_body import MAX_REQUEST_BODY_BYTES, RequestBodyLimitMiddleware
from app.request_id import RequestIDMiddleware
from app.security_headers import SecurityHeadersMiddleware
from app.sidebar.router import router as sidebar_router
from app.smart_create.router import router as smart_create_router
from app.tasks.errors import TaskError
from app.tasks.internal_router import router as internal_tasks_router
from app.tasks.router import router as tasks_router
from app.templates.router import router as templates_router


async def global_exception_handler(
    request: Request,
    exc: Exception,  # ruff: ignore[unused-function-argument] — FastAPI signature requirement
) -> JSONResponse:
    logger.error(
        "Unhandled exception on {method} {path}",
        method=request.method,
        path=request.url.path,
    )
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
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception:  # ruff: ignore[blind-except] — health reports degraded, never 500.
        result["status"] = "degraded"
        result["database"] = "unavailable"
    return result


async def health_check() -> dict[str, str]:
    return await _health_check(settings)


def create_app(app_settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncGenerator[None]:
        configure_logging(json=app_settings.LOG_JSON, level=app_settings.LOG_LEVEL)
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            instance.state.http_client = http_client
            yield
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
    application.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)
    application.add_middleware(GZipMiddleware, minimum_size=1000)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
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
    application.add_middleware(RequestIDMiddleware)
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
    application.include_router(tasks_router)
    application.include_router(internal_tasks_router)

    if app_settings is settings:
        application.get("/health", include_in_schema=False)(health_check)
    else:

        async def configured_health_check() -> dict[str, str]:
            return await _health_check(app_settings)

        application.get("/health", include_in_schema=False)(configured_health_check)

    return application


app = create_app(settings)
