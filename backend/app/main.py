import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.auth.router import router as auth_router
from app.config import settings
from app.database import async_session
from app.internal import internal_router
from app.pages import pages_router
from app.permissions import permissions_router
from app.webhooks import webhooks_router
from app.search import search_router
from app.workspaces import workspaces_router
from app.webhooks.service import register_watch_channel

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Simple in-memory rate limiter (per-IP, sliding window)
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_REQUESTS = 200  # max requests per window
RATE_LIMIT_WINDOW = 60  # seconds


def _is_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    timestamps = _rate_limit_store[client_ip]
    # Prune old entries
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limit_store[client_ip] = [t for t in timestamps if t > cutoff]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        return True
    _rate_limit_store[client_ip].append(now)
    # Prevent unbounded growth: if too many IPs tracked, clear the store
    if len(_rate_limit_store) > 10000:
        _rate_limit_store.clear()
    return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import delete
        from app.models import Page

        async with async_session() as db:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            result = await db.execute(
                delete(Page)
                .where(Page.google_drive_id.like("pending-%"))
                .where(Page.created_at < cutoff)
            )
            if result.rowcount:
                await db.commit()
                log.info("Startup: cleaned up %d stale pending pages", result.rowcount)
    except Exception:
        log.exception("Failed to clean up stale pending pages on startup")

    yield


app = FastAPI(title="Sarj Docs API", lifespan=lifespan)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Skip rate limiting for health, auth, and internal endpoints
    path = request.url.path
    if path in ("/health",) or path.startswith("/internal/") or path.startswith("/auth/"):
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
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(internal_router)
app.include_router(pages_router)
app.include_router(permissions_router)
app.include_router(webhooks_router)
app.include_router(search_router)
app.include_router(workspaces_router)


@app.get("/health")
async def health_check() -> dict:
    from sqlalchemy import text

    result: dict = {"status": "ok"}
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        result["database"] = "ok"
    except Exception:
        result["status"] = "degraded"
        result["database"] = "unavailable"
    return result
