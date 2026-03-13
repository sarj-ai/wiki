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
    _escape_drive_query,
    batch_check_trashed,
    create_folder,
    create_google_doc,
    ensure_folder,
    get_drive_changes,
    get_drive_changes_start_token,
    get_sa_drive_service,
    is_drive_folder_empty,
    move_drive_file,
    trash_drive_file,
)
from app.models.page import Page
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership
from app.pages.schemas import (
    PageCreate,
    PageTreeNode,
    PageUpdate,
    PublishRequest,
    _UNSET,
)

log = logging.getLogger(__name__)

# Prevent concurrent sync operations — only one sync at a time.
_sync_lock = asyncio.Lock()

# Module-level: last known Drive Changes API token.
# Persists within a Cloud Run instance; resets on cold start (triggers full scan).
_last_changes_token: str | None = None

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_tree(pages: list[Page]) -> list[PageTreeNode]:
    """Build a nested tree from a flat list of Page rows."""
    node_map: dict[UUID, PageTreeNode] = {}
    children_map: dict[UUID | None, list[PageTreeNode]] = {}

    for p in pages:
        node = PageTreeNode(
            id=p.id,
            parent_id=p.parent_id,
            title=p.title,
            type=p.type,
            icon=p.icon,
            is_private=p.is_private,
            workspace_id=p.workspace_id,
            sort_order=p.sort_order,
            google_drive_id=p.google_drive_id,
        )
        node_map[p.id] = node
        children_map.setdefault(p.parent_id, []).append(node)

    for node in node_map.values():
        node.children = sorted(
            children_map.get(node.id, []),
            key=lambda n: n.sort_order,
        )

    return sorted(
        children_map.get(None, []),
        key=lambda n: n.sort_order,
    )


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


async def get_page_tree(db: AsyncSession, user: User) -> list[PageTreeNode]:
    """Return all pages visible to the user, assembled into a nested tree."""
    visibility = visible_pages_filter(user)
    result = await db.execute(
        select(Page).where(visibility).order_by(Page.sort_order)
    )
    pages = list(result.scalars().all())
    return _build_tree(pages)


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


async def create_page(
    db: AsyncSession,
    user: User,
    data: PageCreate,
) -> Page:
    """Create a new page.

    1. Resolves the correct Drive folder synchronously (creates children
       folder on Drive if the parent doesn't have one yet).
    2. Saves the page to DB with the correct drive_folder_id.
    3. Creates the Google Doc on Drive.
    4. If doc creation fails, the page is saved with a pending-* ID
       so the user can retry later — but the folder is always correct.
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
        # Private root page → user's private folder
        def _resolve_private_folder() -> str:
            service = get_sa_drive_service()
            private_root = ensure_folder(service, "Private", settings.DRIVE_ROOT_FOLDER_ID)
            return ensure_folder(service, user.email, private_root)

        target_folder_id = await asyncio.to_thread(_resolve_private_folder)

    if target_folder_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot determine Drive folder for this page",
        )

    # --- Step 2: Save page to DB with correct folder ---
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

    # Doc creation happens in the background — return immediately
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
        log.exception("Background: Drive doc creation failed for page %s", page_id)


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
        # Private root page → user's private folder
        def _resolve_private_folder() -> str:
            service = get_sa_drive_service()
            private_root = ensure_folder(service, "Private", settings.DRIVE_ROOT_FOLDER_ID)
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
    from app.docs_transformer import enrich_chips_from_html, transform_doc
    from app.drive import export_doc_html, get_doc_content

    log.info("Content: fetching page %s", page_id)
    page = await get_page(db, user, page_id)
    log.info("Content: got page %s, drive_id=%s", page_id, page.google_drive_id)

    # Drive doc is still being created in background
    if page.google_drive_id.startswith("pending-"):
        return {"blocks": [], "cached": False, "pending": True}

    if not force_refresh and page.cached_content is not None and page.content_cached_at is not None:
        # Treat cache as stale after 5 min (safety net if webhooks didn't fire)
        age = (datetime.now(timezone.utc) - page.content_cached_at).total_seconds()
        if age < 300:
            log.info("Content: returning cached for %s (age %.0fs)", page_id, age)
            return {"blocks": page.cached_content, "cached": True}

    # Fetch from Google Docs API (no lock/semaphore — keep it simple)
    log.info("Content: calling Google Docs API for %s", page_id)
    raw_doc = await asyncio.to_thread(get_doc_content, user, page.google_drive_id)
    log.info("Content: Google API returned for %s", page_id)

    blocks = transform_doc(raw_doc)
    log.info("Content: transformed %d blocks for %s", len(blocks), page_id)

    has_chips = any(b.get("type") == "table" and b.get("hasChips") for b in blocks)
    if has_chips:
        try:
            html = await asyncio.to_thread(export_doc_html, user, page.google_drive_id)
            blocks = enrich_chips_from_html(blocks, html)
            log.info("Enriched smart chips for page %s from HTML export", page_id)
        except Exception:
            log.warning("HTML export failed for page %s, smart chips unresolved", page_id, exc_info=True)

    page.cached_content = blocks
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

    return {"blocks": blocks, "cached": False}


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
                    private_root = ensure_folder(svc, "Private", settings.DRIVE_ROOT_FOLDER_ID)
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


GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"


async def sync_drive_state(db: AsyncSession, user: User) -> dict[str, int]:
    """Sync DB with Drive: remove trashed pages, update titles, ingest new Docs.

    Tries incremental sync via the Drive Changes API first (much faster).
    Falls back to a full folder scan on first run or if incremental fails.
    Skips if another sync is already running (returns zeros).
    """
    if _sync_lock.locked():
        log.info("Sync already in progress, skipping")
        return {"removed": 0, "added": 0, "updated": 0}

    async with _sync_lock:
        global _last_changes_token

        # Try incremental sync first (much faster)
        if _last_changes_token is not None:
            result = await _incremental_sync(db, user, _last_changes_token)
            if result is not None:
                return result

        # Fall back to full scan (first run or incremental failed)
        result = await _full_sync(db, user)

        # Save token for future incremental syncs
        try:
            _last_changes_token = await asyncio.to_thread(get_drive_changes_start_token)
        except Exception:
            log.warning("Failed to get changes start token", exc_info=True)

        return result


async def _incremental_sync(
    db: AsyncSession, user: User, start_token: str
) -> dict[str, int] | None:
    """Try an incremental sync using the Drive Changes API.

    Returns a result dict on success, or None to signal a fallback to full scan.
    """
    global _last_changes_token

    try:
        changes, new_token = await asyncio.to_thread(get_drive_changes, start_token)
    except Exception:
        log.warning("Incremental sync failed, falling back to full scan", exc_info=True)
        _last_changes_token = None  # Reset so next attempt does full scan
        return None  # Signal to fall back to full scan

    if not changes:
        _last_changes_token = new_token
        return {"removed": 0, "added": 0, "updated": 0, "pruned": 0, "cleaned": 0, "mode": "incremental"}

    added = 0
    removed = 0
    updated = 0

    for change in changes:
        file_id = change.get("fileId")
        if not file_id:
            continue  # Drive-level change (not file-specific), skip

        try:
            is_removed = change.get("removed", False)
            file_meta = change.get("file")

            if is_removed or (file_meta and file_meta.get("trashed")):
                # File was removed/trashed — delete from DB if we have it
                result = await db.execute(select(Page).where(Page.google_drive_id == file_id))
                page = result.scalar_one_or_none()
                if page:
                    await db.delete(page)
                    removed += 1
                    log.info("Incremental: removed page '%s' (%s)", page.title, file_id)
                continue

            if file_meta is None:
                continue

            mime_type = file_meta.get("mimeType", "")
            file_name = file_meta.get("name", "Untitled")
            parents = file_meta.get("parents", [])

            if mime_type == GOOGLE_DOC_MIME and parents:
                parent_folder_id = parents[0]

                # Check if we already have this page
                result = await db.execute(select(Page).where(Page.google_drive_id == file_id))
                existing = result.scalar_one_or_none()

                if existing:
                    changed = False
                    if existing.title != file_name:
                        existing.title = file_name
                        changed = True
                    # Clear content cache so it gets re-fetched
                    existing.content_cached_at = None
                    if changed:
                        existing.updated_at = datetime.now(timezone.utc)
                        updated += 1
                        log.info("Incremental: updated '%s' (%s)", file_name, file_id)
                    else:
                        # Title unchanged but content may have changed — still invalidate cache
                        existing.updated_at = datetime.now(timezone.utc)
                        updated += 1
                        log.info("Incremental: invalidated cache for '%s' (%s)", file_name, file_id)
                else:
                    # New doc — need to figure out workspace/parent from folder
                    # Look up which workspace owns this folder
                    ws_result = await db.execute(
                        select(Workspace).where(Workspace.drive_folder_id == parent_folder_id)
                    )
                    workspace = ws_result.scalar_one_or_none()

                    # Check if any page uses this folder as children_folder_id
                    parent_result = await db.execute(
                        select(Page).where(
                            (Page.children_folder_id == parent_folder_id)
                            | ((Page.drive_folder_id == parent_folder_id) & (Page.type == "section"))
                        )
                    )
                    parent_page = parent_result.scalars().first()

                    workspace_id = workspace.id if workspace else (parent_page.workspace_id if parent_page else None)
                    is_private = workspace_id is None
                    parent_page_id = parent_page.id if parent_page else None

                    # Check if it's in a private folder
                    if not workspace and not parent_page:
                        # Could be in Private/{email}/ — check parent folder
                        # For simplicity, mark as private with no parent
                        is_private = True

                    sort_result = await db.execute(
                        select(func.coalesce(func.max(Page.sort_order), 0.0)).where(
                            Page.parent_id == parent_page_id
                        )
                    )
                    sort_order = sort_result.scalar_one() + 1.0

                    new_page = Page(
                        id=uuid4(),
                        parent_id=parent_page_id,
                        google_drive_id=file_id,
                        drive_folder_id=parent_folder_id,
                        title=file_name,
                        type="page",
                        sort_order=sort_order,
                        is_private=is_private,
                        created_by=user.id,
                        workspace_id=workspace_id,
                    )
                    db.add(new_page)
                    added += 1
                    log.info("Incremental: added new doc '%s' (%s)", file_name, file_id)

            elif mime_type == GOOGLE_FOLDER_MIME:
                # Folder changes — we don't need to do much here.
                # The important thing is if a folder was trashed (handled above).
                pass

        except Exception:
            log.warning("Incremental: failed to process change for %s, skipping", file_id, exc_info=True)
            continue

    if added or removed or updated:
        await db.commit()

    _last_changes_token = new_token
    return {"removed": removed, "added": added, "updated": updated, "pruned": 0, "cleaned": 0, "mode": "incremental"}


async def _full_sync(db: AsyncSession, user: User) -> dict[str, int]:
    # --- Phase 1: Remove trashed/deleted pages ---
    visibility = visible_pages_filter(user)
    result = await db.execute(select(Page).where(visibility))
    pages = list(result.scalars().all())

    removed = 0
    # Exclude pages still being created (pending-* Drive IDs don't exist in Drive yet)
    ready_pages = [p for p in pages if not p.google_drive_id.startswith("pending-")]
    if ready_pages:
        drive_ids = [p.google_drive_id for p in ready_pages]
        trashed_map = await asyncio.to_thread(batch_check_trashed, user, drive_ids)
        for page in ready_pages:
            if trashed_map.get(page.google_drive_id, False):
                log.info("Sync: removing page %s (Drive file %s trashed/deleted)", page.id, page.google_drive_id)
                await db.delete(page)
                removed += 1
        if removed:
            await db.commit()

    # --- Phase 2: Discover new Docs + update titles of existing ones ---
    added, updated = await _scan_drive_folder(db, user, settings.DRIVE_ROOT_FOLDER_ID)

    # --- Phase 3: Remove childless section pages from DB ---
    # Section pages represent Drive folders discovered during scan.
    # If a section has no page descendants, it means the folder has no
    # Google Docs inside — remove the section from the sidebar (DB) but
    # leave the Drive folder untouched.
    result = await db.execute(
        select(Page).where(Page.type == "section")
    )
    sections = list(result.scalars().all())

    pruned = 0
    for section in sections:
        child_result = await db.execute(
            select(Page.id).where(Page.parent_id == section.id).limit(1)
        )
        if child_result.scalar_one_or_none() is not None:
            continue  # Has children, keep it
        await db.delete(section)
        pruned += 1
        log.info("Sync: pruned empty section '%s' from sidebar", section.title)

    if pruned:
        await db.commit()

    # --- Phase 4: Clean up orphan children folders in Drive ---
    # Non-section pages may have children_folder_id set (created by
    # _ensure_children_folder) but no children left in DB. If the Drive
    # folder is also truly empty, trash it to keep Drive tidy.
    result = await db.execute(
        select(Page).where(
            Page.children_folder_id.isnot(None),
            Page.type != "section",
        )
    )
    parents_with_folders = list(result.scalars().all())

    cleaned = 0
    for parent in parents_with_folders:
        child_result = await db.execute(
            select(Page.id).where(Page.parent_id == parent.id).limit(1)
        )
        if child_result.scalar_one_or_none() is not None:
            continue  # Has children, skip

        # No children in DB — check if the Drive folder is actually empty
        try:
            empty = await asyncio.to_thread(is_drive_folder_empty, parent.children_folder_id)
        except Exception:
            continue

        if empty:
            try:
                await asyncio.to_thread(trash_drive_file, user, parent.children_folder_id)
                log.info("Sync: trashed empty children folder %s for page '%s'", parent.children_folder_id, parent.title)
            except Exception:
                log.warning("Sync: failed to trash folder %s", parent.children_folder_id, exc_info=True)
                continue
            parent.children_folder_id = None
            cleaned += 1

    if cleaned:
        await db.commit()

    return {"removed": removed, "added": added, "updated": updated, "pruned": pruned, "cleaned": cleaned}


def _list_drive_folder(folder_id: str) -> list[dict]:
    """List all files in a Drive folder (handles pagination).

    Runs synchronously — designed to be called from a worker thread.
    Creates its own thread-local Drive service to avoid httplib2 concurrency issues.
    Returns a list of file metadata dicts.
    """
    service = get_sa_drive_service()
    files: list[dict] = []
    page_token = None
    query = f"'{_escape_drive_query(folder_id)}' in parents and trashed = false"
    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken,files(id,name,mimeType,parents)",
            spaces="drive",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=100,
            pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


async def _scan_drive_folder(
    db: AsyncSession,
    user: User,
    root_folder_id: str,
) -> tuple[int, int]:
    """Recursively scan a Drive folder: ingest new Docs and update titles of existing ones.

    Returns (added, updated).
    """
    added = 0
    updated = 0

    # Build a map of existing pages by Drive ID for title sync
    result = await db.execute(select(Page))
    existing_pages = {p.google_drive_id: p for p in result.scalars().all()}

    # Scan folders recursively using a stack with parallel Drive API calls.
    # Each entry: (folder_id, parent_page_id, is_private, owner_user, workspace_id, folder_path)
    SCAN_CONCURRENCY = 5
    stack: list[tuple[str, UUID | None, bool, User | None, UUID | None, list[str]]] = [
        (root_folder_id, None, False, None, None, [])
    ]

    async def _fetch_one(fid: str) -> list[dict] | None:
        try:
            return await asyncio.to_thread(_list_drive_folder, fid)
        except Exception:
            log.warning("Sync: failed to list folder %s, skipping", fid, exc_info=True)
            return None

    while stack:
        # Pop up to SCAN_CONCURRENCY items for parallel fetching
        batch: list[tuple[str, UUID | None, bool, User | None, UUID | None, list[str]]] = []
        while stack and len(batch) < SCAN_CONCURRENCY:
            batch.append(stack.pop())

        # Fetch all folder listings in parallel (Drive API calls only)
        results = await asyncio.gather(*[_fetch_one(entry[0]) for entry in batch])

        # Process results sequentially (DB operations are not thread-safe)
        for entry, file_list in zip(batch, results):
            if file_list is None:
                continue
            folder_id, parent_page_id, is_private, owner_user, workspace_id, folder_path = entry

            for file_meta in file_list:
                file_id = file_meta["id"]
                file_name = file_meta.get("name", "Untitled")
                mime_type = file_meta.get("mimeType", "")

                if mime_type == GOOGLE_FOLDER_MIME:
                    sub_private = is_private
                    sub_owner = owner_user
                    sub_workspace_id = workspace_id
                    sub_path = folder_path + [file_name]

                    if not folder_path and file_name == "Private":
                        sub_private = True
                    elif len(folder_path) == 1 and folder_path[0] == "Private":
                        sub_private = True
                        r = await db.execute(select(User).where(User.email == file_name))
                        sub_owner = r.scalar_one_or_none()

                    if not is_private and not folder_path:
                        r = await db.execute(
                            select(Workspace).where(Workspace.drive_folder_id == file_id)
                        )
                        ws = r.scalar_one_or_none()
                        if ws:
                            sub_workspace_id = ws.id

                    sub_parent_page_id = parent_page_id
                    r = await db.execute(
                        select(Page).where(
                            (Page.children_folder_id == file_id)
                            | ((Page.drive_folder_id == file_id) & (Page.type == "section"))
                        )
                    )
                    existing_section = r.scalars().first()
                    if existing_section:
                        sub_parent_page_id = existing_section.id
                    else:
                        # Check if a sibling page with the same name exists —
                        # this folder is likely its children_folder created by
                        # _ensure_children_folder. Claim it instead of creating
                        # a duplicate section.
                        sibling_r = await db.execute(
                            select(Page).where(
                                Page.parent_id == parent_page_id,
                                Page.drive_folder_id == folder_id,
                                Page.title == file_name,
                                Page.children_folder_id.is_(None),
                            )
                        )
                        sibling_page = sibling_r.scalars().first()
                        if sibling_page:
                            sibling_page.children_folder_id = file_id
                            sub_parent_page_id = sibling_page.id
                            updated += 1
                            log.info("Sync: claimed folder '%s' (%s) as children of page %s", file_name, file_id, sibling_page.id)

                    if sub_parent_page_id == parent_page_id and (sub_workspace_id is not None or sub_private):
                        # Folder doesn't match any existing page — create a
                        # section so nested docs get proper hierarchy.
                        # Skip special folders (Private, user email folders,
                        # workspace root folders).
                        is_special = (
                            (not folder_path and file_name == "Private")
                            or (len(folder_path) == 1 and folder_path[0] == "Private")
                            or (not folder_path and sub_workspace_id is not None)
                        )
                        if not is_special:
                            sort_result = await db.execute(
                                select(func.coalesce(func.max(Page.sort_order), 0.0)).where(
                                    Page.parent_id == parent_page_id
                                )
                            )
                            sort_order = sort_result.scalar_one() + 1.0

                            section_page = Page(
                                id=uuid4(),
                                parent_id=parent_page_id,
                                google_drive_id=f"section-{file_id}",
                                drive_folder_id=folder_id,
                                children_folder_id=file_id,
                                title=file_name,
                                type="section",
                                sort_order=sort_order,
                                is_private=sub_private,
                                created_by=sub_owner.id if sub_owner else user.id,
                                workspace_id=sub_workspace_id,
                            )
                            db.add(section_page)
                            existing_pages[f"section-{file_id}"] = section_page
                            sub_parent_page_id = section_page.id
                            added += 1
                            log.info("Sync: created section '%s' for folder %s", file_name, file_id)

                    stack.append((file_id, sub_parent_page_id, sub_private, sub_owner, sub_workspace_id, sub_path))

                elif mime_type == GOOGLE_DOC_MIME:
                    existing_page = existing_pages.get(file_id)
                    if existing_page:
                        changed = False
                        # Sync title
                        if existing_page.title != file_name:
                            log.info("Sync: title updated '%s' -> '%s' (%s)", existing_page.title, file_name, file_id)
                            existing_page.title = file_name
                            existing_page.content_cached_at = None
                            changed = True
                        # Fix parent_id based on actual Drive folder location
                        if existing_page.parent_id != parent_page_id:
                            log.info("Sync: re-parented '%s' (%s) from %s to %s", file_name, file_id, existing_page.parent_id, parent_page_id)
                            existing_page.parent_id = parent_page_id
                            changed = True
                        # Fix drive_folder_id
                        if existing_page.drive_folder_id != folder_id:
                            existing_page.drive_folder_id = folder_id
                            changed = True
                        # Fix workspace_id
                        if existing_page.workspace_id != workspace_id:
                            existing_page.workspace_id = workspace_id
                            existing_page.is_private = is_private
                            changed = True
                        if changed:
                            existing_page.updated_at = datetime.now(timezone.utc)
                            updated += 1
                    else:
                        # New Google Doc — ingest it
                        sort_result = await db.execute(
                            select(func.coalesce(func.max(Page.sort_order), 0.0)).where(
                                Page.parent_id == parent_page_id
                            )
                        )
                        sort_order = sort_result.scalar_one() + 1.0

                        new_page = Page(
                            id=uuid4(),
                            parent_id=parent_page_id,
                            google_drive_id=file_id,
                            drive_folder_id=folder_id,
                            title=file_name,
                            type="page",
                            sort_order=sort_order,
                            is_private=is_private,
                            created_by=owner_user.id if owner_user else user.id,
                            workspace_id=workspace_id,
                        )
                        db.add(new_page)
                        existing_pages[file_id] = new_page
                        added += 1
                        log.info("Sync: ingested new doc '%s' (%s)", file_name, file_id)

    if added or updated:
        await db.commit()

    return added, updated
