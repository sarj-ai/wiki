from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import assert_page_owner
from app.models.page import Page, PagePermission
from app.models.user import User
from app.permissions.schemas import PermissionCreate


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _get_page_or_403(db: AsyncSession, page_id: UUID) -> Page:
    """Fetch a page by id or raise 403 (never 404 — don't leak existence)."""
    result = await db.execute(select(Page).where(Page.id == page_id))
    page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this page",
        )
    return page


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def list_permissions(
    db: AsyncSession,
    user: User,
    page_id: UUID,
) -> list[PagePermission]:
    """List all permissions for a page. User must own the page."""
    page = await _get_page_or_403(db, page_id)
    assert_page_owner(page, user, action="manage permissions for")

    result = await db.execute(
        select(PagePermission).where(PagePermission.page_id == page_id)
    )
    return list(result.scalars().all())


async def grant_permission(
    db: AsyncSession,
    user: User,
    data: PermissionCreate,
) -> PagePermission:
    """Grant permission on a page. Only the page owner can grant permissions."""
    page = await _get_page_or_403(db, data.page_id)
    assert_page_owner(page, user, action="manage permissions for")

    # Check for an existing identical permission to prevent duplicates
    filters = [
        PagePermission.page_id == data.page_id,
        PagePermission.permission == data.permission,
    ]
    if data.user_id is not None:
        filters.append(PagePermission.user_id == data.user_id)
    else:
        filters.append(PagePermission.user_id.is_(None))
    if data.workspace_id is not None:
        filters.append(PagePermission.workspace_id == data.workspace_id)
    else:
        filters.append(PagePermission.workspace_id.is_(None))

    existing = await db.execute(select(PagePermission).where(and_(*filters)))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This permission already exists",
        )

    perm = PagePermission(
        id=uuid4(),
        page_id=data.page_id,
        user_id=data.user_id,
        workspace_id=data.workspace_id,
        permission=data.permission,
    )

    db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return perm


async def revoke_permission(
    db: AsyncSession,
    user: User,
    permission_id: UUID,
) -> None:
    """Revoke a permission. Only the page owner can revoke."""
    result = await db.execute(
        select(PagePermission).where(PagePermission.id == permission_id)
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission not found or access denied",
        )

    # Load the page to verify ownership
    page = await _get_page_or_403(db, perm.page_id)
    assert_page_owner(page, user, action="manage permissions for")

    await db.delete(perm)
    await db.commit()
