import time
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.auth.router import router as auth_router
from app.config import settings
from app.database import async_session
from app.drive.search_router import router as drive_search_router
from app.extras.router import router as extras_router
from app.files.router import router as files_router
from app.logging_config import configure_logging
from app.sidebar.router import router as sidebar_router

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
    cutoff = now - RATE_LIMIT_WINDOW
    _rate_limit_store[client_ip] = [t for t in timestamps if t > cutoff]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        return True
    _rate_limit_store[client_ip].append(now)
    _rate_limit_last_seen[client_ip] = now
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_IPS:
        stale_cutoff = now - _RATE_LIMIT_EVICT_AGE
        stale_ips = [ip for ip, ts in _rate_limit_last_seen.items() if ts < stale_cutoff]
        for ip in stale_ips:
            _rate_limit_store.pop(ip, None)
            _rate_limit_last_seen.pop(ip, None)
    return False


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging(json=settings.LOG_JSON, level=settings.LOG_LEVEL)
    yield


app = FastAPI(title="Sarj Navigator API", lifespan=lifespan)


@app.middleware("http")
async def rate_limit_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path
    if path in ("/health",) or path in ("/auth/login", "/auth/callback", "/auth/logout"):
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
    # Without this, unhandled exceptions produce a plain-text response missing
    # CORS headers, so the browser blocks the response and hides the real error.
    logger.exception("Unhandled exception on {} {}", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


app.include_router(auth_router)
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
