"""Page content fetching and caching."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.docs_transformer import enrich_chips_from_html, transform_doc_with_tabs
from app.drive import export_doc_html, get_doc_content, get_drive_service
from app.models.user import User
from app.search.service import update_search_vector

log = logging.getLogger(__name__)


async def get_page_content(
    db: AsyncSession,
    user: User,
    page_id: UUID,
    *,
    force_refresh: bool = False,
) -> dict:
    """Fetch the rendered content of a page's Google Doc.

    Uses caching -- content is only re-fetched from Google when stale or forced.
    A per-page lock deduplicates concurrent requests so only one hits the API.
    """
    from app.pages.crud import get_page

    log.info("Content: fetching page %s", page_id)
    page = await get_page(db, user, page_id)
    log.info("Content: got page %s, drive_id=%s", page_id, page.google_drive_id)

    # Drive doc is still being created in background
    if page.google_drive_id.startswith("pending-"):
        return {"tabs": [], "cached": False, "pending": True}

    if not force_refresh and page.cached_content is not None and page.content_cached_at is not None:
        # Treat cache as stale after 60s to keep embedded image URLs fresh
        age = (datetime.now(timezone.utc) - page.content_cached_at).total_seconds()
        if age < 60:
            log.info("Content: returning cached for %s (age %.0fs)", page_id, age)
            cached = page.cached_content
            # Migrate old flat-blocks cache format to tabs format
            if isinstance(cached, list) and cached and isinstance(cached[0], dict) and "type" in cached[0]:
                cached = [{"title": "", "blocks": cached}]
            return {"tabs": cached, "cached": True}

    # Fetch from Google Docs API (no lock/semaphore -- keep it simple)
    log.info("Content: calling Google Docs API for %s", page_id)
    raw_doc = await asyncio.to_thread(get_doc_content, user, page.google_drive_id)
    log.info("Content: Google API returned for %s", page_id)

    tabs = transform_doc_with_tabs(raw_doc)
    log.info("Content: transformed %d tabs for %s", len(tabs), page_id)

    # Enrich smart chips in all tabs via per-tab HTML export
    tabs_with_chips = [
        tab for tab in tabs
        if tab.get("tabId")
        and any(b.get("type") == "table" and b.get("hasChips") for b in tab.get("blocks", []))
    ]
    for tab in tabs_with_chips:
        try:
            html = await asyncio.to_thread(
                export_doc_html, user, page.google_drive_id, tab_id=tab["tabId"],
            )
            enrich_chips_from_html(tab["blocks"], html)
            log.info("Enriched smart chips for page %s tab %s", page_id, tab["tabId"])
        except Exception:
            log.warning(
                "HTML export failed for page %s tab %s, smart chips unresolved",
                page_id, tab["tabId"], exc_info=True,
            )

    page.cached_content = tabs
    page.content_cached_at = datetime.now(timezone.utc)

    # Update last-edited metadata from Drive only on forced refresh.
    # Uses the user's credentials (not service account) to see lastModifyingUser.
    if force_refresh:
        try:
            user_service = get_drive_service(user)
            file_meta = await asyncio.to_thread(
                lambda: user_service.files().get(
                    fileId=page.google_drive_id,
                    fields="modifiedTime,lastModifyingUser(emailAddress)",
                    supportsAllDrives=True,
                ).execute()
            )
            drive_modified = file_meta.get("modifiedTime")
            if drive_modified:
                drive_dt = datetime.fromisoformat(drive_modified.replace("Z", "+00:00"))
                # Only update if Drive timestamp is newer than what we have
                if page.updated_at is None or abs((drive_dt - page.updated_at).total_seconds()) > 2:
                    page.updated_at = drive_dt
            editor_email = (file_meta.get("lastModifyingUser") or {}).get("emailAddress")
            if editor_email and not editor_email.endswith(".iam.gserviceaccount.com"):
                result = await db.execute(select(User).where(User.email == editor_email))
                editor = result.scalar_one_or_none()
                if editor and page.last_edited_by != editor.id:
                    page.last_edited_by = editor.id
        except Exception:
            log.debug("Failed to fetch Drive metadata for %s", page_id, exc_info=True)

    await db.commit()

    # Update search vector with title + content text
    try:
        await update_search_vector(db, page)
        await db.commit()
    except Exception:
        await db.rollback()
        log.warning("Failed to update search vector for page %s", page.id)

    return {"tabs": tabs, "cached": False}
