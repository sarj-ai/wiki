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
    return _build_tree(pages)
