"""Drive webhook endpoint — receives push notifications from Google."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Header, Response
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.drive_webhook import DriveWebhookChannel
from app.webhooks.service import process_drive_notification

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/drive")
async def drive_webhook(
    background_tasks: BackgroundTasks,
    x_goog_channel_id: str | None = Header(None),
    x_goog_resource_id: str | None = Header(None),
    x_goog_resource_state: str | None = Header(None),
    x_goog_channel_token: str | None = Header(None),
) -> Response:
    """Receive Google Drive push notifications.

    Google sends a POST with custom headers (not a JSON body).
    We must return 200 quickly, then process asynchronously.
    """
    log.info(
        "Drive webhook: channel=%s resource=%s state=%s",
        x_goog_channel_id,
        x_goog_resource_id,
        x_goog_resource_state,
    )

    # Reject requests missing required Google headers
    if not x_goog_channel_id or not x_goog_resource_id:
        return Response(status_code=400)

    # Verify the channel token matches our configured secret
    if settings.DRIVE_WEBHOOK_TOKEN:
        if x_goog_channel_token != settings.DRIVE_WEBHOOK_TOKEN:
            log.warning(
                "Drive webhook token mismatch for channel=%s — rejecting",
                x_goog_channel_id,
            )
            return Response(status_code=403)

    # Verify the resource ID matches the stored channel record
    async with async_session() as db:
        result = await db.execute(
            select(DriveWebhookChannel).where(
                DriveWebhookChannel.channel_id == x_goog_channel_id
            )
        )
        channel = result.scalar_one_or_none()
        if channel is None:
            log.warning(
                "Drive webhook from unknown channel %s — rejecting",
                x_goog_channel_id,
            )
            return Response(status_code=403)
        if channel.resource_id != x_goog_resource_id:
            log.warning(
                "Drive webhook resource_id mismatch: header=%s stored=%s — rejecting",
                x_goog_resource_id,
                channel.resource_id,
            )
            return Response(status_code=403)

    # Google sends a "sync" message when the channel is first created — ignore it
    if x_goog_resource_state == "sync":
        return Response(status_code=200)

    background_tasks.add_task(
        process_drive_notification,
        x_goog_channel_id,
        x_goog_resource_id,
    )

    return Response(status_code=200)
