"""Drive webhook service — register channels, process change notifications."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.drive import get_drive_service
from app.models.drive_webhook import DriveWebhookChannel
from app.models.page import Page
from app.models.user import User
from app.models.workspace import Workspace

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

    service = get_drive_service(user)

    # Get a start page token — we'll only receive changes after this point
    token_resp = await asyncio.to_thread(
        service.changes().getStartPageToken().execute
    )
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

    resp = await asyncio.to_thread(
        service.changes().watch(pageToken=start_page_token, body=body).execute
    )

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
        service = get_drive_service(user)
        await asyncio.to_thread(
            service.channels().stop(body={
                "id": channel.channel_id,
                "resourceId": channel.resource_id,
            }).execute
        )
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

        service = get_drive_service(user)

        if not channel.page_token:
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
        resp = await asyncio.to_thread(
            service.changes().list(
                pageToken=page_token,
                fields="nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,trashed,parents))",
                includeRemoved=True,
                spaces="drive",
            ).execute
        )

        changes = resp.get("changes", [])
        for change in changes:
            await _handle_single_change(db, service, change)

        # Move to next page of changes, or save the new start token
        if "newStartPageToken" in resp:
            channel.page_token = resp["newStartPageToken"]
            page_token = None  # no more pages
        else:
            page_token = resp.get("nextPageToken")

    await db.commit()
    log.info("Processed Drive changes, new page_token=%s", channel.page_token)


async def _handle_single_change(db: AsyncSession, service, change: dict) -> None:
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
        if page:
            log.info("Drive file %s permanently deleted — removing page %s", file_id, page.id)
            await db.delete(page)
        return

    file_meta = change.get("file", {})

    if file_meta.get("trashed"):
        if page:
            log.info("Drive file %s trashed — removing page %s", file_id, page.id)
            await db.delete(page)
        return

    if page:
        # App is the source of truth for titles — push our title to Drive
        # if they diverge, rather than overwriting from Drive.
        drive_name = file_meta.get("name")
        if drive_name and drive_name != page.title:
            log.info("Drive file %s has name '%s' but app has '%s' — renaming in Drive", file_id, drive_name, page.title)
            try:
                from app.drive import rename_drive_file, get_sa_drive_service
                await asyncio.to_thread(rename_drive_file, None, file_id, page.title)
            except Exception:
                log.warning("Failed to rename Drive file %s to match app title", file_id, exc_info=True)

        # Invalidate content cache so the next viewer fetches fresh from Google
        if page.cached_content is not None or page.content_cached_at is not None:
            log.info("Invalidated content cache for page %s (drive file %s)", page.id, file_id)
            page.cached_content = None
            page.content_cached_at = None

        page.updated_at = datetime.now(timezone.utc)
        return

    # --- New file: try to ingest it ---
    await _try_ingest_new_file(db, service, file_id, file_meta)


GOOGLE_DOC_MIME = "application/vnd.google-apps.document"


async def _try_ingest_new_file(
    db: AsyncSession, service, file_id: str, file_meta: dict
) -> None:
    """Ingest a new Google Doc discovered via Drive webhook into our pages table."""
    mime_type = file_meta.get("mimeType", "")
    if mime_type != GOOGLE_DOC_MIME:
        return  # Only ingest Google Docs

    parents = file_meta.get("parents", [])
    if not parents:
        return  # File must have a parent folder

    parent_folder_id = parents[0]
    title = file_meta.get("name", "Untitled")

    # Skip if a pending page exists in this folder (background creation in progress)
    pending_result = await db.execute(
        select(Page).where(
            Page.google_drive_id.like("pending-%"),
            Page.drive_folder_id == parent_folder_id,
        )
    )
    if pending_result.scalar_one_or_none():
        log.info("Skipping webhook ingest for %s — pending page exists in folder %s", file_id, parent_folder_id)
        return

    # Walk up the folder chain to verify this file lives under our root folder
    # and to determine if it's in a Private/{email} path
    folder_chain = await _resolve_folder_chain(service, parent_folder_id)
    if not folder_chain:
        log.debug("Drive file %s is not under our root folder — skipping", file_id)
        return

    # Determine privacy and ownership from folder path
    is_private = False
    owner_user: User | None = None
    workspace_id: uuid.UUID | None = None

    # folder_chain is [root_child, ..., immediate_parent] — names from root downward
    if len(folder_chain) >= 2 and folder_chain[0]["name"] == "My Drafts":
        is_private = True
        # Second folder in chain is the user email folder
        owner_email = folder_chain[1]["name"]
        result = await db.execute(select(User).where(User.email == owner_email))
        owner_user = result.scalar_one_or_none()
    else:
        # Public doc — try to match to a workspace by its Drive folder
        for folder in folder_chain:
            result = await db.execute(
                select(Workspace).where(Workspace.drive_folder_id == folder["id"])
            )
            ws = result.scalar_one_or_none()
            if ws:
                workspace_id = ws.id
                break

    # Try to find a parent page whose drive_folder_id or children_folder_id matches
    parent_page_id: uuid.UUID | None = None
    result = await db.execute(
        select(Page).where(
            (Page.children_folder_id == parent_folder_id)
            | (Page.drive_folder_id == parent_folder_id)
        )
    )
    parent_page = result.scalars().first()
    if parent_page:
        parent_page_id = parent_page.id

    # Sort order: append after last sibling
    sibling_filter = [Page.parent_id == parent_page_id]
    if workspace_id:
        sibling_filter.append(Page.workspace_id == workspace_id)
    elif owner_user:
        sibling_filter.append(Page.created_by == owner_user.id)

    result = await db.execute(
        select(func.coalesce(func.max(Page.sort_order), 0.0)).where(*sibling_filter)
    )
    sort_order = result.scalar_one() + 1.0

    new_page = Page(
        id=uuid.uuid4(),
        parent_id=parent_page_id,
        google_drive_id=file_id,
        drive_folder_id=parent_folder_id,
        title=title,
        type="page",
        sort_order=sort_order,
        is_private=is_private,
        created_by=owner_user.id if owner_user else None,
        workspace_id=workspace_id,
    )
    db.add(new_page)
    log.info(
        "Ingested new Drive doc '%s' (%s) as page %s (private=%s, workspace=%s)",
        title, file_id, new_page.id, is_private, workspace_id,
    )


async def _resolve_folder_chain(service, folder_id: str) -> list[dict] | None:
    """Walk up the Drive folder tree from folder_id to our root folder.

    Returns a list of folder dicts [{id, name}, ...] from the root's child
    down to the given folder_id (inclusive). Returns None if the chain doesn't
    lead to our root folder (file is outside our scope).
    """
    root_id = settings.DRIVE_ROOT_FOLDER_ID
    chain: list[dict] = []
    current_id = folder_id
    # Safety limit to prevent infinite loops
    max_depth = 20

    for _ in range(max_depth):
        if current_id == root_id:
            chain.reverse()
            return chain

        try:
            meta = await asyncio.to_thread(
                service.files().get(
                    fileId=current_id, fields="id,name,parents"
                ).execute
            )
        except Exception:
            log.debug("Cannot access folder %s — stopping chain walk", current_id)
            return None

        chain.append({"id": meta["id"], "name": meta["name"]})
        parents = meta.get("parents", [])
        if not parents:
            return None  # Reached a root with no parents — not under our folder
        current_id = parents[0]

    return None  # Exceeded max depth


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
