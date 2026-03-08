"""Drive webhook service — register channels, process change notifications."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption import decrypt_token
from app.config import settings
from app.database import async_session
from app.models.drive_webhook import DriveWebhookChannel
from app.models.page import Page
from app.models.user import User

log = logging.getLogger(__name__)

# Channel lifetime — Google allows max 24 hours for Drive push notifications.
CHANNEL_TTL = timedelta(hours=12)


# ------------------------------------------------------------------
# Drive service helpers
# ------------------------------------------------------------------

async def _get_any_user(db: AsyncSession) -> User | None:
    """Get any user with a refresh token to act on behalf of the app."""
    result = await db.execute(
        select(User).where(User.encrypted_refresh_token.isnot(None)).limit(1)
    )
    return result.scalar_one_or_none()


def _build_drive_service(user: User):
    access_token = decrypt_token(user.encrypted_access_token)
    refresh_token = (
        decrypt_token(user.encrypted_refresh_token)
        if user.encrypted_refresh_token
        else None
    )
    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
    )
    return build("drive", "v3", credentials=creds, static_discovery=True)


# ------------------------------------------------------------------
# Channel registration
# ------------------------------------------------------------------

async def register_watch_channel(db: AsyncSession) -> DriveWebhookChannel | None:
    """Register a Drive push notification channel using the Changes API.

    Returns the created channel row, or None if webhook URL is not configured.
    """
    if not settings.WEBHOOK_BASE_URL:
        log.info("WEBHOOK_BASE_URL not set — skipping webhook registration")
        return None

    user = await _get_any_user(db)
    if user is None:
        log.warning("No user with refresh token — cannot register webhook")
        return None

    service = _build_drive_service(user)

    # Get a start page token — we'll only receive changes after this point
    token_resp = service.changes().getStartPageToken().execute()
    start_page_token = token_resp["startPageToken"]

    channel_id = str(uuid.uuid4())
    expiry = datetime.now(timezone.utc) + CHANNEL_TTL
    expiry_ms = int(expiry.timestamp() * 1000)

    webhook_url = f"{settings.WEBHOOK_BASE_URL}/webhooks/drive"

    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "expiration": expiry_ms,
    }

    resp = service.changes().watch(
        pageToken=start_page_token,
        body=body,
    ).execute()

    channel = DriveWebhookChannel(
        channel_id=resp["id"],
        resource_id=resp["resourceId"],
        folder_id=settings.DRIVE_ROOT_FOLDER_ID,
        page_token=start_page_token,
        expiry=expiry,
    )

    # Remove any old channels first
    await db.execute(delete(DriveWebhookChannel))
    db.add(channel)
    await db.commit()

    log.info(
        "Registered Drive webhook channel %s (token=%s, expires=%s)",
        channel.channel_id,
        start_page_token,
        channel.expiry,
    )
    return channel


async def stop_channel(db: AsyncSession, channel: DriveWebhookChannel, user: User) -> None:
    """Stop a Drive watch channel."""
    try:
        service = _build_drive_service(user)
        service.channels().stop(body={
            "id": channel.channel_id,
            "resourceId": channel.resource_id,
        }).execute()
    except Exception:
        log.exception("Failed to stop channel %s", channel.channel_id)

    await db.execute(
        delete(DriveWebhookChannel).where(
            DriveWebhookChannel.channel_id == channel.channel_id
        )
    )
    await db.commit()


# ------------------------------------------------------------------
# Process incoming notifications
# ------------------------------------------------------------------

async def process_drive_notification(
    channel_id: str,
    resource_id: str,
) -> None:
    """Handle a Drive push notification.

    Fetches changes since our last page token and syncs the DB.
    """
    async with async_session() as db:
        # Verify this is a known channel
        result = await db.execute(
            select(DriveWebhookChannel).where(
                DriveWebhookChannel.channel_id == channel_id
            )
        )
        channel = result.scalar_one_or_none()
        if channel is None:
            log.warning("Unknown webhook channel %s — ignoring", channel_id)
            return

        user = await _get_any_user(db)
        if user is None:
            log.warning("No user available to process Drive changes")
            return

        service = _build_drive_service(user)

        page_token = channel.page_token
        if not page_token:
            log.warning("No page_token for channel %s — skipping", channel_id)
            return

        try:
            await _process_changes(db, service, channel)
        except Exception:
            log.exception("Failed to process Drive changes for channel %s", channel_id)


async def _process_changes(
    db: AsyncSession,
    service,
    channel: DriveWebhookChannel,
) -> None:
    """Fetch and process all pending changes from the Changes API."""
    page_token = channel.page_token

    while page_token:
        resp = service.changes().list(
            pageToken=page_token,
            fields="nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,trashed,parents))",
            includeRemoved=True,
            spaces="drive",
        ).execute()

        changes = resp.get("changes", [])
        for change in changes:
            await _handle_single_change(db, change)

        # Move to next page of changes, or save the new start token
        if "newStartPageToken" in resp:
            channel.page_token = resp["newStartPageToken"]
            page_token = None  # no more pages
        else:
            page_token = resp.get("nextPageToken")

    await db.commit()
    log.info("Processed Drive changes, new page_token=%s", channel.page_token)


async def _handle_single_change(db: AsyncSession, change: dict) -> None:
    """Process a single change entry from the Changes API."""
    file_id = change.get("fileId")
    if not file_id:
        return

    # Check if this file exists in our DB
    result = await db.execute(
        select(Page).where(Page.google_drive_id == file_id)
    )
    page = result.scalar_one_or_none()

    if change.get("removed"):
        # File was permanently deleted
        if page:
            log.info("Drive file %s permanently deleted — removing page %s", file_id, page.id)
            await db.delete(page)
        return

    file_meta = change.get("file", {})

    if file_meta.get("trashed"):
        # File was trashed
        if page:
            log.info("Drive file %s trashed — removing page %s", file_id, page.id)
            await db.delete(page)
        return

    if page and file_meta.get("name"):
        # File was renamed in Drive — sync title to our DB
        new_name = file_meta["name"]
        if new_name != page.title:
            log.info("Drive file %s renamed to '%s' — updating page %s", file_id, new_name, page.id)
            page.title = new_name
            page.updated_at = datetime.now(timezone.utc)


# ------------------------------------------------------------------
# Background renewal task
# ------------------------------------------------------------------

async def renew_expiring_channels() -> None:
    """Check for channels expiring soon and re-register them.

    Called periodically by APScheduler.
    """
    async with async_session() as db:
        threshold = datetime.now(timezone.utc) + timedelta(hours=2)
        result = await db.execute(
            select(DriveWebhookChannel).where(
                DriveWebhookChannel.expiry < threshold
            )
        )
        expiring = list(result.scalars().all())

        if not expiring:
            return

        log.info("Renewing %d expiring webhook channel(s)", len(expiring))

        user = await _get_any_user(db)
        if user is None:
            log.warning("No user available for channel renewal")
            return

        for channel in expiring:
            await stop_channel(db, channel, user)

        await register_watch_channel(db)
