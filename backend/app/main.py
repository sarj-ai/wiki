import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.router import router as auth_router
from app.config import settings
from app.database import async_session
from app.drive.search_router import router as drive_search_router
from app.extras import extras_router
from app.files import files_router
from app.imports import imports_router
from app.internal import internal_router
from app.pages import pages_router
from app.permissions import permissions_router
from app.search import search_router
from app.sidebar import sidebar_router
from app.webhooks import webhooks_router
from app.webhooks.service import register_watch_channel
from app.workspaces import workspaces_router

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter (per-IP, sliding window)
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_rate_limit_last_seen: dict[str, float] = {}  # IP -> last request time
RATE_LIMIT_REQUESTS = 200  # max requests per window
RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_IPS = 10000
_RATE_LIMIT_EVICT_AGE = 120  # evict IPs not seen in 2 minutes


def _is_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    timestamps = _rate_limit_store[client_ip]
    # Prune old entries for this IP
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limit_store[client_ip] = [t for t in timestamps if t > cutoff]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        return True
    _rate_limit_store[client_ip].append(now)
    _rate_limit_last_seen[client_ip] = now
    # Evict stale IPs individually instead of clearing everything
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_IPS:
        stale_cutoff = now - _RATE_LIMIT_EVICT_AGE
        stale_ips = [ip for ip, ts in _rate_limit_last_seen.items() if ts < stale_cutoff]
        for ip in stale_ips:
            _rate_limit_store.pop(ip, None)
            _rate_limit_last_seen.pop(ip, None)
    return False


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Startup
    logging.basicConfig(level=logging.INFO)

    # Register Drive webhook channel on startup (if WEBHOOK_BASE_URL is set)
    if settings.WEBHOOK_BASE_URL:
        try:
            async with async_session() as db:
                await register_watch_channel(db)
        except Exception:
            log.exception("Failed to register Drive webhook on startup")
    else:
        log.info("WEBHOOK_BASE_URL not set — Drive webhooks disabled")

    # Clean up stale pending pages (failed Drive doc creation)
    try:
        from datetime import datetime, timedelta

        from sqlalchemy import delete

        from app.models import Page

        async with async_session() as db:
            cutoff = datetime.now(UTC) - timedelta(days=7)
            result = await db.execute(
                delete(Page)
                .where(Page.google_drive_id.like("pending-%"))
                .where(Page.created_at < cutoff)
            )
            rowcount = result.rowcount  # type: ignore[attr-defined]
            if rowcount:
                await db.commit()
                log.info("Startup: cleaned up %d stale pending pages", rowcount)
    except Exception:
        log.exception("Failed to clean up stale pending pages on startup")

    yield


app = FastAPI(title="Sarj Docs API", lifespan=lifespan)


@app.middleware("http")
async def rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Skip rate limiting for health, internal, and auth login/callback (not refresh)
    path = request.url.path
    if (
        path in ("/health",)
        or path.startswith("/internal/")
        or path in ("/auth/login", "/auth/callback", "/auth/logout")
    ):
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many requests. Please slow down."},
        )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,  # noqa: ARG001 — FastAPI signature requirement
) -> JSONResponse:
    """Catch unhandled exceptions and return a proper JSON 500 response.

    Without this, unhandled exceptions produce a plain-text response that
    doesn't include CORS headers, causing the browser to block the response
    entirely (hiding the actual error from the frontend).
    """
    log.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.include_router(auth_router)
app.include_router(imports_router)
app.include_router(internal_router)
app.include_router(pages_router)
app.include_router(permissions_router)
app.include_router(webhooks_router)
app.include_router(search_router)
app.include_router(workspaces_router)

# Navigator routers — coexist with legacy routers on the feature branch.
# Sub-PR C5 deletes the legacy ones.
app.include_router(sidebar_router)
app.include_router(files_router)
app.include_router(extras_router)
app.include_router(drive_search_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    from sqlalchemy import text

    result: dict[str, str] = {"status": "ok"}
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception:
        result["status"] = "degraded"
        result["database"] = "unavailable"
    return result
