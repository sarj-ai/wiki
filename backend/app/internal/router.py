"""Internal endpoints called by Cloud Scheduler (not by users)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.database import async_session
from app.internal.dependencies import verify_scheduler

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(verify_scheduler)],
)


@router.post("/renew-webhooks")
async def renew_webhooks() -> dict:
    """Renew expiring Drive webhook channels."""
    from app.webhooks.service import renew_expiring_channels

    await renew_expiring_channels()
    return {"status": "ok"}


@router.post("/reindex")
async def reindex() -> dict:
    """Rebuild search vectors for all pages from cached content."""
    from app.search.service import reindex_all

    async with async_session() as db:
        count = await reindex_all(db)

    log.info("Reindex complete: %d pages (via Cloud Scheduler)", count)
    return {"reindexed": count}


@router.post("/sync-workspaces")
async def sync_workspaces() -> dict:
    """Sync Google Groups, folder permissions, and user memberships."""
    from app.workspaces.service import (
        sync_all_groups,
        sync_all_user_memberships,
        sync_folder_permissions,
    )

    async with async_session() as db:
        await sync_all_groups(db)
        await sync_folder_permissions(db)
        await sync_all_user_memberships(db)

    log.info("Workspace sync complete (via Cloud Scheduler)")
    return {"status": "ok"}
