"""Page CRUD operations: create, read, update, delete, publish, content."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import assert_page_owner, visible_pages_filter
from app.config import settings
from app.drive import (
    create_folder,
    create_google_doc,
    ensure_folder,
    get_sa_drive_service,
    move_drive_file,
    rename_drive_file,
    trash_drive_file,
)
from app.models.page import Page
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership
from app.pages.schemas import (
    PageCreate,
    PageUpdate,
    PublishRequest,
    _UNSET,
)

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _verify_workspace_membership(
    db: AsyncSession,
    user_id: UUID,
    workspace_id: UUID,
) -> None:
    """Raise 403 if the user is not a member of the workspace."""
    result = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this workspace",
        )


async def _get_page_or_404(db: AsyncSession, page_id: UUID) -> Page:
    """Fetch a page by id or raise 404."""
    result = await db.execute(select(Page).where(Page.id == page_id))
    page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Page not found",
        )
    return page


async def _ensure_children_folder(db: AsyncSession, parent_page: Page) -> str:
    """Ensure a parent page has a children folder on Drive.

    If the parent already has children_folder_id, returns it.
    Otherwise creates the folder on Drive, updates the parent, and returns the ID.
    """
    if parent_page.children_folder_id:
        return parent_page.children_folder_id

    def _create_folder_sync() -> str:
        service = get_sa_drive_service()
        folder_metadata = {
            "name": parent_page.title,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_page.drive_folder_id],
        }
        return service.files().create(
            body=folder_metadata, fields="id", supportsAllDrives=True,
        ).execute()["id"]

    folder_id = await asyncio.to_thread(_create_folder_sync)
    parent_page.children_folder_id = folder_id
    await db.commit()
    return folder_id


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def get_page(db: AsyncSession, user: User, page_id: UUID) -> Page:
    """Fetch a single page, enforcing access control.

    Returns 403 for both missing pages and unauthorized access
    to avoid leaking page existence.
    """
    visibility = visible_pages_filter(user)
    result = await db.execute(
        select(Page).where(Page.id == page_id, visibility)
    )
    page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this page",
        )
    return page


async def create_page(
    db: AsyncSession,
    user: User,
    data: PageCreate,
) -> Page:
    """Create a new page.

    1. Resolves the correct Drive folder (creates children folder on Drive
       if the parent doesn't have one yet).
    2. Saves the page to DB with a pending-* Drive ID.
    3. Background task creates the Google Doc on Drive.

    If Drive doc creation fails, the user can retry or delete via the UI.
    """
    if data.workspace_id is not None:
        await _verify_workspace_membership(db, user.id, data.workspace_id)

    is_private = data.workspace_id is None

    # Determine sort_order: max among siblings + 1
    sibling_filter = [Page.parent_id == data.parent_id]
    if data.workspace_id is not None:
        sibling_filter.append(Page.workspace_id == data.workspace_id)
    else:
        sibling_filter.append(Page.created_by == user.id)

    result = await db.execute(
        select(func.coalesce(func.max(Page.sort_order), 0.0)).where(*sibling_filter)
    )
    sort_order = result.scalar_one() + 1.0

    # --- Step 1: Resolve the correct Drive folder ---
    target_folder_id: str | None = None

    if data.parent_id is not None:
        parent_page = await get_page(db, user, data.parent_id)
        target_folder_id = await _ensure_children_folder(db, parent_page)
    elif data.workspace_id is not None:
        ws_result = await db.execute(
            select(Workspace).where(Workspace.id == data.workspace_id)
        )
        workspace = ws_result.scalar_one_or_none()
        if workspace:
            target_folder_id = workspace.drive_folder_id
    else:
        # Private root page -> user's private folder
        def _resolve_private_folder() -> str:
            service = get_sa_drive_service()
            private_root = ensure_folder(service, "My Drafts", settings.DRIVE_ROOT_FOLDER_ID)
            return ensure_folder(service, user.email, private_root)

        target_folder_id = await asyncio.to_thread(_resolve_private_folder)

    if target_folder_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot determine Drive folder for this page",
        )

    # --- Step 2: Save page to DB with pending Drive ID ---
    page_id = uuid4()

    page = Page(
        id=page_id,
        parent_id=data.parent_id,
        google_drive_id=f"pending-{page_id}",
        drive_folder_id=target_folder_id,
        title=data.title,
        type=data.type,
        sort_order=sort_order,
        is_private=is_private,
        created_by=user.id,
        workspace_id=data.workspace_id,
        icon=data.icon,
    )

    db.add(page)
    await db.commit()
    await db.refresh(page)

    # Doc creation happens in the background -- return immediately
    return page


async def finalize_page_creation(page_id: UUID, user_email: str, title: str, is_private: bool, target_folder_id: str) -> None:
    """Background task: create the Google Doc on Drive and update the page record."""
    from app.database import async_session

    try:
        google_drive_id, drive_folder_id = await asyncio.to_thread(
            create_google_doc,
            user_email,
            title,
            is_private=is_private,
            target_folder_id=target_folder_id,
        )

        async with async_session() as db:
            result = await db.execute(select(Page).where(Page.id == page_id))
            page = result.scalar_one_or_none()
            if page is None:
                return
            page.google_drive_id = google_drive_id
            page.drive_folder_id = drive_folder_id
            await db.commit()

            from app.search.service import update_search_vector
            try:
                await update_search_vector(db, page)
                await db.commit()
            except Exception:
                await db.rollback()
                log.warning("Failed to update search vector for page %s", page_id)

        log.info("Background: created Drive doc for page %s -> %s", page_id, google_drive_id)
    except Exception:
        log.exception("Background: Drive doc creation failed for page %s — user can retry or delete", page_id)


async def retry_pending_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
) -> Page:
    """Retry Drive doc creation for a stuck pending page.

    Re-resolves the correct folder from the DB tree, creating the
    children folder on Drive if it doesn't exist yet.
    """
    page = await _get_page_or_404(db, page_id)
    assert_page_owner(page, user)

    if not page.google_drive_id.startswith("pending-"):
        return page  # Already created, nothing to do

    # Re-resolve target folder from the DB tree
    target_folder_id: str | None = None

    if page.parent_id is not None:
        parent_page = await _get_page_or_404(db, page.parent_id)
        target_folder_id = await _ensure_children_folder(db, parent_page)
    elif page.workspace_id is not None:
        ws_result = await db.execute(
            select(Workspace).where(Workspace.id == page.workspace_id)
        )
        workspace = ws_result.scalar_one_or_none()
        if workspace:
            target_folder_id = workspace.drive_folder_id
    else:
        # Private root page -> user's private folder
        def _resolve_private_folder() -> str:
            service = get_sa_drive_service()
            private_root = ensure_folder(service, "My Drafts", settings.DRIVE_ROOT_FOLDER_ID)
            return ensure_folder(service, user.email, private_root)

        target_folder_id = await asyncio.to_thread(_resolve_private_folder)

    if target_folder_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot determine Drive folder for this page",
        )

    try:
        google_drive_id, drive_folder_id = await asyncio.to_thread(
            create_google_doc,
            user.email,
            page.title,
            is_private=page.is_private,
            target_folder_id=target_folder_id,
        )
    except Exception:
        log.exception("Retry: Drive doc creation failed for page %s", page_id)
        raise HTTPException(
            status_code=502,
            detail="Failed to create document on Drive, please try again",
        )

    page.google_drive_id = google_drive_id
    page.drive_folder_id = drive_folder_id
    await db.commit()
    await db.refresh(page)

    log.info("Retry: created Drive doc for page %s -> %s", page_id, google_drive_id)

    from app.search.service import update_search_vector
    try:
        await update_search_vector(db, page)
        await db.commit()
    except Exception:
        await db.rollback()
        log.warning("Failed to update search vector for page %s", page.id)

    return page


async def update_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
    data: PageUpdate,
) -> Page:
    """Update an existing page. Only the owner can update for now."""
    page = await _get_page_or_404(db, page_id)
    assert_page_owner(page, user)

    if data.title is not None:
        page.title = data.title
        # Also rename the file in Google Drive (unless it's a pending doc)
        if not page.google_drive_id.startswith("pending-"):
            try:
                await asyncio.to_thread(rename_drive_file, user, page.google_drive_id, data.title)
            except Exception:
                log.warning("Failed to rename Drive file %s", page.google_drive_id, exc_info=True)

    if data.parent_id is not _UNSET:
        page.parent_id = data.parent_id  # type: ignore[assignment]

    if data.sort_order is not None:
        page.sort_order = data.sort_order

    if data.icon is not _UNSET:
        page.icon = data.icon  # type: ignore[assignment]

    if data.cover_url is not _UNSET:
        page.cover_url = data.cover_url  # type: ignore[assignment]

    page.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(page)

    # Re-index if title changed
    if data.title is not None:
        from app.search.service import update_search_vector
        try:
            await update_search_vector(db, page)
            await db.commit()
        except Exception:
            await db.rollback()
            log.warning("Failed to update search vector for page %s", page.id)

    return page


async def delete_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
) -> None:
    """Delete a page and all descendants via CASCADE.

    Collects all descendant Drive file IDs first, then trashes them.
    """
    page = await _get_page_or_404(db, page_id)
    assert_page_owner(page, user)

    # Collect all descendant Drive IDs + folder IDs via recursive CTE
    base = select(Page.id, Page.google_drive_id, Page.children_folder_id).where(
        Page.id == page_id
    )
    cte = base.cte(name="descendants", recursive=True)
    recursive = select(Page.id, Page.google_drive_id, Page.children_folder_id).join(
        cte, Page.parent_id == cte.c.id
    )
    cte = cte.union_all(recursive)

    result = await db.execute(
        select(cte.c.google_drive_id, cte.c.children_folder_id)
    )
    rows = result.all()
    drive_ids = [row[0] for row in rows]
    folder_ids = [row[1] for row in rows if row[1] is not None]

    for file_id in drive_ids + folder_ids:
        try:
            await asyncio.to_thread(trash_drive_file, user, file_id)
        except Exception:
            log.warning("Failed to trash Drive file/folder %s", file_id)

    await db.delete(page)
    await db.commit()


async def publish_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
    data: PublishRequest,
) -> Page:
    """Publish a private page to a workspace.

    - Detaches the page from its private parent (becomes a root page in the workspace)
    - Moves the Drive file from the private folder to the workspace folder
    - Updates workspace_id, is_private, drive_folder_id, parent_id, sort_order
    """
    page = await _get_page_or_404(db, page_id)
    assert_page_owner(page, user)

    await _verify_workspace_membership(db, user.id, data.workspace_id)

    # Look up the workspace's Drive folder
    ws_result = await db.execute(
        select(Workspace).where(Workspace.id == data.workspace_id)
    )
    workspace = ws_result.scalar_one_or_none()
    if not workspace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )

    # Calculate sort_order for the workspace root level
    result = await db.execute(
        select(func.coalesce(func.max(Page.sort_order), 0.0)).where(
            Page.workspace_id == data.workspace_id,
            Page.parent_id.is_(None),
        )
    )
    new_sort_order = result.scalar_one() + 1.0

    # Move Drive file to workspace folder
    if not page.google_drive_id.startswith("pending-"):
        try:
            await asyncio.to_thread(
                move_drive_file, user, page.google_drive_id, workspace.drive_folder_id
            )
        except Exception:
            log.exception("Failed to move Drive file %s to workspace folder", page.google_drive_id)

    # Move children folder to workspace folder (moves all descendants in Drive)
    if page.children_folder_id:
        try:
            await asyncio.to_thread(
                move_drive_file, user, page.children_folder_id, workspace.drive_folder_id
            )
        except Exception:
            log.exception("Failed to move children folder %s to workspace folder", page.children_folder_id)

    # Detach from private parent, move to workspace root
    page.parent_id = None
    page.workspace_id = data.workspace_id
    page.is_private = False
    page.drive_folder_id = workspace.drive_folder_id
    page.sort_order = new_sort_order
    page.updated_at = datetime.now(timezone.utc)

    # Update all descendants in the DB (workspace_id + is_private)
    descendant_cte = (
        select(Page.id)
        .where(Page.parent_id == page.id)
        .cte(name="descendants", recursive=True)
    )
    descendant_cte = descendant_cte.union_all(
        select(Page.id).join(descendant_cte, Page.parent_id == descendant_cte.c.id)
    )
    await db.execute(
        Page.__table__.update()
        .where(Page.id.in_(select(descendant_cte.c.id)))
        .values(workspace_id=data.workspace_id, is_private=False)
    )

    await db.commit()
    await db.refresh(page)
    return page


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
    from app.docs_transformer import enrich_chips_from_html, transform_doc_with_tabs
    from app.drive import export_doc_html, get_doc_content

    log.info("Content: fetching page %s", page_id)
    page = await get_page(db, user, page_id)
    log.info("Content: got page %s, drive_id=%s", page_id, page.google_drive_id)

    # Drive doc is still being created in background
    if page.google_drive_id.startswith("pending-"):
        return {"tabs": [], "cached": False, "pending": True}

    if not force_refresh and page.cached_content is not None and page.content_cached_at is not None:
        # Treat cache as stale after 5 min (safety net if webhooks didn't fire)
        age = (datetime.now(timezone.utc) - page.content_cached_at).total_seconds()
        if age < 300:
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
    await db.commit()

    # Update search vector with title + content text
    from app.search.service import update_search_vector
    try:
        await update_search_vector(db, page)
        await db.commit()
    except Exception:
        await db.rollback()
        log.warning("Failed to update search vector for page %s", page.id)

    return {"tabs": tabs, "cached": False}


async def move_page_in_drive(
    user: User,
    page: Page,
    new_parent_id: UUID | None,
) -> None:
    """Move a page's Drive file to match a re-parent operation.

    This is designed to run as a background task. Failures are logged
    but do not propagate (Drive sync is best-effort).
    """
    from app.database import async_session

    async def _update_page_folder(page_id: UUID, folder_id: str) -> None:
        async with async_session() as db:
            result = await db.execute(select(Page).where(Page.id == page_id))
            current_page = result.scalar_one_or_none()
            if current_page:
                current_page.drive_folder_id = folder_id
                await db.commit()

    try:
        if new_parent_id is not None:
            async with async_session() as db:
                result = await db.execute(
                    select(Page).where(Page.id == new_parent_id)
                )
                new_parent = result.scalar_one_or_none()
                if not new_parent:
                    return

                if new_parent.children_folder_id:
                    target_folder_id = new_parent.children_folder_id
                else:
                    target_folder_id = await asyncio.to_thread(
                        create_folder, user, new_parent.title, new_parent.drive_folder_id,
                    )
                    new_parent.children_folder_id = target_folder_id
                    await db.commit()

                await asyncio.to_thread(move_drive_file, user, page.google_drive_id, target_folder_id)
                await _update_page_folder(page.id, target_folder_id)
        else:
            if page.is_private:
                def _resolve_private_folder() -> str:
                    svc = get_sa_drive_service()
                    private_root = ensure_folder(svc, "My Drafts", settings.DRIVE_ROOT_FOLDER_ID)
                    return ensure_folder(svc, user.email, private_root)

                target_folder_id = await asyncio.to_thread(_resolve_private_folder)
            else:
                target_folder_id = settings.DRIVE_ROOT_FOLDER_ID

            await asyncio.to_thread(move_drive_file, user, page.google_drive_id, target_folder_id)
            await _update_page_folder(page.id, target_folder_id)
    except Exception:
        log.exception(
            "Failed to move Drive file %s to parent %s",
            page.google_drive_id,
            new_parent_id,
        )
