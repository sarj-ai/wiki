"""Page tree building and retrieval."""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import visible_pages_filter
from app.models.page import Page
from app.models.user import User
from app.pages.schemas import PageTreeNode

log = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Tree helpers
# ------------------------------------------------------------------

def _build_tree(
    pages: list[Page],
    user_info: dict[UUID, tuple[str, str | None]] | None = None,
) -> list[PageTreeNode]:
    """Build a nested tree from a flat list of Page rows.

    ``user_info`` maps user ID → (email, display_name).
    """
    node_map: dict[UUID, PageTreeNode] = {}
    children_map: dict[UUID | None, list[PageTreeNode]] = {}

    for p in pages:
        editor_info = (user_info or {}).get(p.last_edited_by) if p.last_edited_by else None
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
            updated_at=p.updated_at,
            last_edited_by_email=editor_info[0] if editor_info else None,
            last_edited_by_name=editor_info[1] if editor_info else None,
        )
        node_map[p.id] = node
        children_map.setdefault(p.parent_id, []).append(node)

    for node in node_map.values():
        node.children = sorted(
            children_map.get(node.id, []),
            key=lambda n: n.sort_order,
        )

    # Collect root nodes + orphans (pages whose parent isn't in the visible set).
    # Orphans are promoted to root level so shared pages with inaccessible
    # parents still appear in the sidebar.
    roots = list(children_map.get(None, []))
    for parent_id, children in children_map.items():
        if parent_id is not None and parent_id not in node_map:
            roots.extend(children)

    return sorted(roots, key=lambda n: n.sort_order)


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def get_page_tree(db: AsyncSession, user: User) -> list[PageTreeNode]:
    """Return all pages visible to the user, assembled into a nested tree."""
    visibility = visible_pages_filter(user)
    result = await db.execute(
        select(Page).where(visibility).order_by(Page.sort_order)
    )
    pages = list(result.scalars().all())

    # Debug: log page count and check for permission-based pages
    from app.models.page import PagePermission
    perm_count = await db.execute(
        select(PagePermission).where(PagePermission.user_id == user.id)
    )
    user_perms = list(perm_count.scalars().all())
    log.info(
        "Tree for %s: %d pages visible, %d direct permissions",
        user.email, len(pages), len(user_perms),
    )
    for up in user_perms:
        log.info("  Perm: page_id=%s, permission=%s", up.page_id, up.permission)

    # Resolve last_edited_by user info (email + display name)
    editor_ids = {p.last_edited_by for p in pages if p.last_edited_by}
    user_info: dict[UUID, tuple[str, str | None]] = {}
    if editor_ids:
        from app.models.user import User as UserModel

        users_result = await db.execute(
            select(UserModel.id, UserModel.email, UserModel.display_name).where(UserModel.id.in_(editor_ids))
        )
        user_info = {row[0]: (row[1], row[2]) for row in users_result.all()}

    return _build_tree(pages, user_info)
