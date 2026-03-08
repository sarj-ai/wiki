"""Drive webhook endpoint — receives push notifications from Google."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Header, Response

from app.webhooks.service import process_drive_notification

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/drive")
async def drive_webhook(
    background_tasks: BackgroundTasks,
    x_goog_channel_id: str | None = Header(None),
    x_goog_resource_id: str | None = Header(None),
    x_goog_resource_state: str | None = Header(None),
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

    # Google sends a "sync" message when the channel is first created — ignore it
    if x_goog_resource_state == "sync":
        return Response(status_code=200)

    if x_goog_channel_id and x_goog_resource_id:
        background_tasks.add_task(
            process_drive_notification,
            x_goog_channel_id,
            x_goog_resource_id,
        )

    return Response(status_code=200)
