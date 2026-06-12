from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
import httpx
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.auth.router import router as auth_router
from app.bootstrap.router import router as bootstrap_router
from app.config import settings
from app.database import async_session, close_connector
from app.drive.search_router import router as drive_search_router
from app.extras.router import router as extras_router
from app.files.errors import FilesError
from app.files.router import router as files_router
from app.logging_config import configure_logging
from app.ratelimit import limiter
from app.request_id import RequestIDMiddleware
from app.sidebar.router import router as sidebar_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    configure_logging(json=settings.LOG_JSON, level=settings.LOG_LEVEL)
    async with httpx.AsyncClient(timeout=10.0) as http_client:
        app.state.http_client = http_client
        yield
    await close_connector()


app = FastAPI(title="Sarj Wiki API", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(RequestIDMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,  # noqa: ARG001 — FastAPI signature requirement
) -> JSONResponse:
    logger.exception("Unhandled exception on {} {}", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.exception_handler(FilesError)
async def files_error_handler(
    _request: Request,
    exc: FilesError,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content={"code": exc.code, "detail": str(exc)},
    )


app.include_router(auth_router)
app.include_router(bootstrap_router)
app.include_router(sidebar_router)
app.include_router(files_router)
app.include_router(extras_router)
app.include_router(drive_search_router)


@app.get("/health")
@limiter.exempt  # type: ignore[untyped-decorator]
async def health_check(_request: Request) -> dict[str, str]:
    result: dict[str, str] = {"status": "ok"}
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception:  # noqa: BLE001 — /health must never 500; report degraded instead.
        result["status"] = "degraded"
        result["database"] = "unavailable"
    return result
