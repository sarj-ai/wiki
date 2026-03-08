"""Shared access-control helpers used across service layers."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.models.page import Page
from app.models.user import User


def assert_page_owner(page: Page, user: User, *, action: str = "modify") -> None:
    """Raise 403 if the user is not the page creator."""
    if page.created_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have permission to {action} this page",
        )
