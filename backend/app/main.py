import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.config import settings
from app.database import async_session
from app.pages import pages_router
from app.permissions import permissions_router
from app.webhooks import webhooks_router
from app.search import search_router
from app.workspaces import workspaces_router
from app.webhooks.service import register_watch_channel, renew_expiring_channels

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    logging.basicConfig(level=logging.INFO)

    # Register Drive webhook channel (if WEBHOOK_BASE_URL is set)
    if settings.WEBHOOK_BASE_URL:
        try:
            async with async_session() as db:
                await register_watch_channel(db)
        except Exception:
            log.exception("Failed to register Drive webhook on startup")

        # Schedule periodic renewal every 6 hours
        scheduler.add_job(
            renew_expiring_channels,
            "interval",
            hours=6,
            id="renew_drive_webhooks",
            replace_existing=True,
        )
        scheduler.start()
        log.info("APScheduler started for webhook renewal")
    else:
        log.info("WEBHOOK_BASE_URL not set — Drive webhooks disabled")

    yield

    # Shutdown
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Sarj Docs API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(permissions_router)
app.include_router(webhooks_router)
app.include_router(search_router)
app.include_router(workspaces_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
