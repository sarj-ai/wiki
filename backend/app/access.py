"""Shared access-control helpers used across service layers."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import or_, select

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


def visible_pages_filter(user: User):
    """Return an OR filter for pages visible to the given user.

    Centralises the three access-control rules:
      1. User created the page (owner always sees own)
      2. Page is public and user is in the page's workspace
      3. User has explicit permission (direct or via workspace)
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
