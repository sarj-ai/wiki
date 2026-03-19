"""Page CRUD operations: create, read, update, delete, publish."""

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
    ensure_folder,
    get_sa_drive_service,
    move_drive_file,
    trash_drive_file,
)
from app.models.page import Page
from app.models.user import User
from app.models.workspace import Workspace
from app.pages.helpers import (
    _ensure_children_folder,
    _get_page_or_403,
    _verify_workspace_membership,
)
from app.pages.schemas import (
    PageCreate,
    PageUpdate,
    PublishRequest,
    _UNSET,
)

log = logging.getLogger(__name__)


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


async def update_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
    data: PageUpdate,
) -> Page:
    """Update an existing page. Owner or workspace member (for synced sections) can update."""
    page = await _get_page_or_403(db, page_id)
    if page.created_by is None:
        # Synced section with no owner — allow if user is a member of the page's workspace
        if page.workspace_id is not None:
            await _verify_workspace_membership(db, user.id, page.workspace_id)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this page",
            )
    else:
        assert_page_owner(page, user)

    if data.title is not None:
        page.title = data.title
        # Drive rename is handled by the router via background task — not here

    if data.parent_id is not _UNSET:
        if data.parent_id is not None:
            # Prevent self-parenting
            if data.parent_id == page_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot parent a page under itself")
            # Verify user can access the target parent
            await get_page(db, user, data.parent_id)
            # Prevent cycle: walk ancestors of target to ensure page_id is not among them
            ancestor_id = data.parent_id
            visited: set[UUID] = set()
            while ancestor_id is not None:
                if ancestor_id in visited:
                    break  # cycle in existing tree — stop walking
                if ancestor_id == page_id:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot parent a page under its own descendant")
                visited.add(ancestor_id)
                anc_result = await db.execute(select(Page.parent_id).where(Page.id == ancestor_id))
                row = anc_result.one_or_none()
                ancestor_id = row[0] if row else None
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
    page = await _get_page_or_403(db, page_id)
    if page.created_by is None:
        if page.workspace_id is not None:
            await _verify_workspace_membership(db, user.id, page.workspace_id)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to modify this page",
            )
    else:
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
    drive_ids = [row[0] for row in rows if not row[0].startswith("pending-") and not row[0].startswith("section-")]
    folder_ids = [row[1] for row in rows if row[1] is not None]

    # Delete from DB first — Drive trashing is best-effort and can't be rolled back
    await db.delete(page)
    await db.commit()

    # Trash Google Doc files (not folders — folders may contain non-Doc files the app doesn't manage)
    for file_id in drive_ids:
        try:
            await asyncio.to_thread(trash_drive_file, user, file_id)
        except Exception:
            log.warning("Failed to trash Drive file %s", file_id)

    # Only trash folders that are now empty (all their contents were our Docs, now trashed)
    for folder_id in folder_ids:
        try:
            from app.drive import is_drive_folder_empty
            empty = await asyncio.to_thread(is_drive_folder_empty, folder_id)
            if empty:
                await asyncio.to_thread(trash_drive_file, user, folder_id)
                log.info("Trashed empty folder %s", folder_id)
            else:
                log.info("Kept folder %s (still has non-Doc contents)", folder_id)
        except Exception:
            log.warning("Failed to check/trash folder %s", folder_id)


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
    page = await _get_page_or_403(db, page_id)
    assert_page_owner(page, user)

    if not page.is_private:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Page is already published to a workspace",
        )

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

    # Detach from private parent, move to workspace root
    google_drive_id = page.google_drive_id
    children_folder_id = page.children_folder_id
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

    # Commit DB first — Drive moves are best-effort
    await db.commit()
    await db.refresh(page)

    # Move Drive files (best-effort, failures logged but DB is already consistent)
    if not google_drive_id.startswith("pending-"):
        try:
            await asyncio.to_thread(
                move_drive_file, user, google_drive_id, workspace.drive_folder_id
            )
        except Exception:
            log.exception("Failed to move Drive file %s to workspace folder", google_drive_id)

    if children_folder_id:
        # Only move the children folder if the page actually has children in the DB
        child_result = await db.execute(
            select(Page.id).where(Page.parent_id == page.id).limit(1)
        )
        has_children = child_result.scalar_one_or_none() is not None
        if has_children:
            try:
                await asyncio.to_thread(
                    move_drive_file, user, children_folder_id, workspace.drive_folder_id
                )
            except Exception:
                log.exception("Failed to move children folder %s to workspace folder", children_folder_id)

    return page
