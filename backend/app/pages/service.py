from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from app.drive import (
    batch_check_trashed,
    create_folder,
    create_google_doc,
    is_drive_file_trashed,
    rename_child_folder,
    rename_drive_file,
    trash_drive_file,
)

log = logging.getLogger(__name__)
from app.models.page import Page, PagePermission
from app.models.user import User
from app.models.workspace import WorkspaceMembership
from app.pages.schemas import (
    PageCreate,
    PageTreeNode,
    PageUpdate,
    PublishRequest,
    _UNSET,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _build_tree(pages: list[Page]) -> list[PageTreeNode]:
    """Build a nested tree from a flat list of Page rows.

    Groups pages by parent_id, then recursively attaches children.
    Returns the list of root nodes (parent_id is None).
    """
    # Index by id for quick lookup
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

    # Attach children to each node
    for node in node_map.values():
        node.children = sorted(
            children_map.get(node.id, []),
            key=lambda n: n.sort_order,
        )

    # Root nodes are those whose parent_id is None
    roots = sorted(
        children_map.get(None, []),
        key=lambda n: n.sort_order,
    )
    return roots


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


def _assert_owner(page: Page, user: User) -> None:
    """Raise 403 if the user is not the page creator."""
    if page.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this page",
        )


async def _visible_pages_filter(user: User):
    """Return the OR filter for pages visible to the given user.

    Centralises the three access-control rules so they can be reused by
    both ``get_page_tree`` and ``get_page``.
    """
    # Sub-query: workspace IDs the user belongs to
    user_workspace_ids = (
        select(WorkspaceMembership.workspace_id)
        .where(WorkspaceMembership.user_id == user.id)
        .correlate()
        .scalar_subquery()
    )

    # Sub-query: page IDs the user has explicit permission on
    permitted_page_ids = (
        select(PagePermission.page_id)
        .where(
            or_(
                PagePermission.user_id == user.id,
                PagePermission.workspace_id.in_(
                    select(WorkspaceMembership.workspace_id).where(
                        WorkspaceMembership.user_id == user.id
                    )
                ),
            )
        )
        .correlate()
        .scalar_subquery()
    )

    return or_(
        # Rule 1: owner
        Page.created_by == user.id,
        # Rule 2: non-private page in user's workspace
        (Page.is_private == False) & Page.workspace_id.in_(user_workspace_ids),  # noqa: E712
        # Rule 3: explicit permission
        Page.id.in_(permitted_page_ids),
    )


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def get_page(db: AsyncSession, user: User, page_id: UUID) -> Page:
    """Fetch a single page, enforcing access control.

    If the page does not exist or the user has no access, raise 403 —
    never 404 so we don't leak existence.
    """
    visibility = await _visible_pages_filter(user)
    stmt = select(Page).where(Page.id == page_id, visibility)
    result = await db.execute(stmt)
    page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this page",
        )
    return page


async def get_page_tree(db: AsyncSession, user: User) -> list[PageTreeNode]:
    """Return all pages visible to the user, assembled into a nested tree.

    Access control rules:
      1. created_by == user.id                                        -> visible
      2. is_private == False AND workspace_id in user's workspaces    -> visible
      3. explicit page_permission for user or user's workspace        -> visible
    """

    visibility = await _visible_pages_filter(user)
    stmt = select(Page).where(visibility).order_by(Page.sort_order)

    result = await db.execute(stmt)
    pages = list(result.scalars().all())

    return _build_tree(pages)


async def create_page(
    db: AsyncSession,
    user: User,
    data: PageCreate,
) -> Page:
    """Create a new page and return it."""

    # If workspace_id provided, verify membership
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
    max_sort = result.scalar_one()
    sort_order = max_sort + 1.0

    # If nesting under a parent, use (or create) the parent's children folder
    target_folder_id: str | None = None
    if data.parent_id is not None:
        parent_page = await _get_page_or_404(db, data.parent_id)
        if parent_page.children_folder_id:
            target_folder_id = parent_page.children_folder_id
        else:
            # First child — create a folder for this parent's children
            target_folder_id = create_folder(
                user, parent_page.title, parent_page.drive_folder_id,
            )
            parent_page.children_folder_id = target_folder_id

    # Create real Google Doc in Drive
    google_drive_id, drive_folder_id = create_google_doc(
        user,
        data.title,
        is_private=is_private,
        target_folder_id=target_folder_id,
    )

    page = Page(
        id=uuid4(),
        parent_id=data.parent_id,
        google_drive_id=google_drive_id,
        drive_folder_id=drive_folder_id,
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
    return page


async def update_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
    data: PageUpdate,
) -> Page:
    """Update an existing page. Only the owner can update for now."""

    page = await _get_page_or_404(db, page_id)
    _assert_owner(page, user)

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
    return page


async def delete_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
) -> None:
    """Delete a page and all descendants. CASCADE handles DB children.

    Collects all descendant Drive file IDs first, then trashes them all.
    """

    page = await _get_page_or_404(db, page_id)
    _assert_owner(page, user)

    # Collect all descendant Drive IDs + folder IDs (recursive CTE) before CASCADE
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

    # Trash all docs and folders in Google Drive (best-effort)
    for file_id in drive_ids + folder_ids:
        try:
            trash_drive_file(user, file_id)
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
    """Publish a private page to a workspace."""

    page = await _get_page_or_404(db, page_id)
    _assert_owner(page, user)

    # Verify user is member of the target workspace
    await _verify_workspace_membership(db, user.id, data.workspace_id)

    page.workspace_id = data.workspace_id
    page.is_private = False
    page.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(page)
    return page


async def sync_drive_state(db: AsyncSession, user: User) -> int:
    """Check all user-visible pages against Drive and remove trashed/deleted ones.

    Returns the number of pages removed.
    """
    visibility = await _visible_pages_filter(user)
    result = await db.execute(select(Page).where(visibility))
    pages = list(result.scalars().all())

    if not pages:
        return 0

    drive_ids = [p.google_drive_id for p in pages]
    trashed_map = batch_check_trashed(user, drive_ids)

    removed = 0
    for page in pages:
        if trashed_map.get(page.google_drive_id, False):
            log.info("Sync: Drive file %s trashed/deleted — removing page %s", page.google_drive_id, page.id)
            await db.delete(page)
            removed += 1

    if removed:
        await db.commit()

    return removed
