from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import assert_page_owner
from app.config import settings
from app.drive import (
    batch_check_trashed,
    create_folder,
    create_google_doc,
    get_drive_service,
    trash_drive_file,
)
from app.models.page import Page, PagePermission
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


def _visible_pages_filter(user: User):
    """Return an OR filter for pages visible to the given user.

    Centralises the three access-control rules so they can be reused by
    both ``get_page_tree`` and ``get_page``.
    """
    user_workspace_ids = (
        select(WorkspaceMembership.workspace_id)
        .where(WorkspaceMembership.user_id == user.id)
        .correlate()
        .scalar_subquery()
    )

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
        Page.created_by == user.id,
        (Page.is_private == False) & Page.workspace_id.in_(user_workspace_ids),  # noqa: E712
        Page.id.in_(permitted_page_ids),
    )


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def get_page(db: AsyncSession, user: User, page_id: UUID) -> Page:
    """Fetch a single page, enforcing access control.

    Returns 403 for both missing pages and unauthorized access
    to avoid leaking page existence.
    """
    visibility = _visible_pages_filter(user)
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
    visibility = _visible_pages_filter(user)
    result = await db.execute(
        select(Page).where(visibility).order_by(Page.sort_order)
    )
    pages = list(result.scalars().all())
    return _build_tree(pages)


async def create_page(
    db: AsyncSession,
    user: User,
    data: PageCreate,
) -> Page:
    """Create a new page and its backing Google Doc."""
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

    # If nesting under a parent, use (or create) the parent's children folder
    target_folder_id: str | None = None
    if data.parent_id is not None:
        parent_page = await _get_page_or_404(db, data.parent_id)
        if parent_page.children_folder_id:
            target_folder_id = parent_page.children_folder_id
        else:
            target_folder_id = create_folder(
                user, parent_page.title, parent_page.drive_folder_id,
            )
            parent_page.children_folder_id = target_folder_id

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

    # Index title for search
    from app.search.service import update_search_vector
    try:
        await update_search_vector(db, page)
        await db.commit()
    except Exception:
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
    assert_page_owner(page, user)

    await _verify_workspace_membership(db, user.id, data.workspace_id)

    page.workspace_id = data.workspace_id
    page.is_private = False
    page.updated_at = datetime.now(timezone.utc)

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
    """
    from app.docs_transformer import enrich_chips_from_html, transform_doc
    from app.drive import export_doc_html, get_doc_content

    page = await get_page(db, user, page_id)

    if not force_refresh and page.cached_content is not None and page.content_cached_at is not None:
        return {"blocks": page.cached_content, "cached": True}

    raw_doc = get_doc_content(user, page.google_drive_id)
    blocks = transform_doc(raw_doc)

    has_chips = any(b.get("type") == "table" and b.get("hasChips") for b in blocks)
    if has_chips:
        try:
            html = export_doc_html(user, page.google_drive_id)
            blocks = enrich_chips_from_html(blocks, html)
        except Exception:
            pass  # Fall back to placeholder if HTML export fails

    page.cached_content = blocks
    page.content_cached_at = datetime.now(timezone.utc)
    await db.commit()

    # Update search vector with title + content text
    from app.search.service import update_search_vector
    try:
        await update_search_vector(db, page)
        await db.commit()
    except Exception:
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
    from app.config import settings
    from app.database import async_session
    from app.drive import ensure_folder, get_drive_service, move_drive_file

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
                    target_folder_id = create_folder(user, new_parent.title, new_parent.drive_folder_id)
                    new_parent.children_folder_id = target_folder_id
                    await db.commit()

                move_drive_file(user, page.google_drive_id, target_folder_id)
                await _update_page_folder(page.id, target_folder_id)
        else:
            if page.is_private:
                drive_service = get_drive_service(user)
                private_root = ensure_folder(drive_service, "Private", settings.DRIVE_ROOT_FOLDER_ID)
                target_folder_id = ensure_folder(drive_service, user.email, private_root)
            else:
                target_folder_id = settings.DRIVE_ROOT_FOLDER_ID

            move_drive_file(user, page.google_drive_id, target_folder_id)
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

    Returns {"removed": N, "added": M, "updated": K}.
    """
    # --- Phase 1: Remove trashed/deleted pages ---
    visibility = _visible_pages_filter(user)
    result = await db.execute(select(Page).where(visibility))
    pages = list(result.scalars().all())

    removed = 0
    if pages:
        drive_ids = [p.google_drive_id for p in pages]
        trashed_map = batch_check_trashed(user, drive_ids)
        for page in pages:
            if trashed_map.get(page.google_drive_id, False):
                log.info("Sync: removing page %s (Drive file %s trashed/deleted)", page.id, page.google_drive_id)
                await db.delete(page)
                removed += 1
        if removed:
            await db.commit()

    # --- Phase 2: Discover new Docs + update titles of existing ones ---
    added, updated = await _scan_drive_folder(db, user, settings.DRIVE_ROOT_FOLDER_ID)

    return {"removed": removed, "added": added, "updated": updated}


async def _scan_drive_folder(
    db: AsyncSession,
    user: User,
    root_folder_id: str,
) -> tuple[int, int]:
    """Recursively scan a Drive folder: ingest new Docs and update titles of existing ones.

    Returns (added, updated).
    """
    service = get_drive_service(user)
    added = 0
    updated = 0

    # Build a map of existing pages by Drive ID for title sync
    result = await db.execute(select(Page))
    existing_pages = {p.google_drive_id: p for p in result.scalars().all()}

    # Scan folders recursively using a stack
    # Each entry: (folder_id, parent_page_id, is_private, owner_user, workspace_id, folder_path)
    stack: list[tuple[str, UUID | None, bool, User | None, UUID | None, list[str]]] = [
        (root_folder_id, None, False, None, None, [])
    ]

    while stack:
        folder_id, parent_page_id, is_private, owner_user, workspace_id, folder_path = stack.pop()

        page_token = None
        while True:
            query = f"'{_escape_drive_query(folder_id)}' in parents and trashed = false"
            resp = service.files().list(
                q=query,
                fields="nextPageToken,files(id,name,mimeType,parents)",
                spaces="drive",
                pageSize=100,
                pageToken=page_token,
            ).execute()

            for file_meta in resp.get("files", []):
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

                    stack.append((file_id, sub_parent_page_id, sub_private, sub_owner, sub_workspace_id, sub_path))

                elif mime_type == GOOGLE_DOC_MIME:
                    existing_page = existing_pages.get(file_id)
                    if existing_page:
                        # Existing page — sync title and invalidate cache
                        if existing_page.title != file_name:
                            log.info("Sync: title updated '%s' -> '%s' (%s)", existing_page.title, file_name, file_id)
                            existing_page.title = file_name
                            existing_page.content_cached_at = None
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

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    if added or updated:
        await db.commit()

    return added, updated


def _escape_drive_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
