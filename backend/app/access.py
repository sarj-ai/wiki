"""Shared access-control helpers used across service layers."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page import Page, PagePermission
from app.models.user import User
from app.models.workspace import WorkspaceMembership


def assert_page_owner(page: Page, user: User, *, action: str = "modify") -> None:
    """Raise 403 if the user is not the page creator."""
    if page.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have permission to {action} this page",
        )


async def assert_can_manage_permissions(
    db: AsyncSession,
    page: Page,
    user: User,
    *,
    action: str = "manage permissions for",
) -> None:
    """Raise 403 unless the user can manage permissions on this page.

    Allowed when:
      1. User is the page creator (owner), OR
      2. Page belongs to a workspace the user is a member of
         (covers synced pages where created_by is NULL).
    """
    if page.created_by == user.id:
        return

    if page.workspace_id is not None:
        result = await db.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.workspace_id == page.workspace_id,
            )
        )
        if result.scalar_one_or_none() is not None:
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"You do not have permission to {action} this page",
    )


def visible_pages_filter(user: User):
    """Return an OR filter for pages visible to the given user.

    Centralises the access-control rules:
      1. User created the page (owner always sees own)
      2. Page is public and user is in the page's workspace
      3. User has explicit permission (direct or via workspace)
      4. An ancestor page has an *inheritable* permission for the user
         (covers recursive sharing — no records copied to descendants)
    """
    user_workspace_ids = (
        select(WorkspaceMembership.workspace_id)
        .where(WorkspaceMembership.user_id == user.id)
        .correlate()
        .scalar_subquery()
    )

    # Direct (non-inherited) permission check
    _user_perm_filter = or_(
        PagePermission.user_id == user.id,
        PagePermission.workspace_id.in_(
            select(WorkspaceMembership.workspace_id).where(
                WorkspaceMembership.user_id == user.id
            )
        ),
    )

    permitted_page_ids = (
        select(PagePermission.page_id)
        .where(_user_perm_filter)
        .correlate()
        .scalar_subquery()
    )

    # Inherited permission check: pages whose *ancestor* has an
    # inheritable permission for this user.  Uses a recursive CTE that
    # starts from pages with inheritable perms and walks *down* the tree.
    inheritable_roots = (
        select(PagePermission.page_id)
        .where(
            PagePermission.inheritable == True,  # noqa: E712
            _user_perm_filter,
        )
    )

    inherited_base = select(Page.id).where(Page.parent_id.in_(inheritable_roots))
    inherited_cte = inherited_base.cte(name="inherited_visible", recursive=True)
    inherited_recursive = select(Page.id).join(
        inherited_cte, Page.parent_id == inherited_cte.c.id
    )
    inherited_cte = inherited_cte.union_all(inherited_recursive)

    inherited_page_ids = select(inherited_cte.c.id).scalar_subquery()

    return or_(
        Page.created_by == user.id,
        (Page.is_private == False) & Page.workspace_id.in_(user_workspace_ids),  # noqa: E712
        Page.id.in_(permitted_page_ids),
        Page.id.in_(inherited_page_ids),
    )
