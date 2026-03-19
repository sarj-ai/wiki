"""Shared helpers for the pages package."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.drive import get_sa_drive_service
from app.models.page import Page
from app.models.workspace import WorkspaceMembership


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


async def _get_page_or_403(db: AsyncSession, page_id: UUID) -> Page:
    """Fetch a page by id or raise 403.

    Uses 403 (not 404) to avoid leaking page existence.
    """
    result = await db.execute(select(Page).where(Page.id == page_id))
    page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this page",
        )
    return page


async def _ensure_children_folder(db: AsyncSession, parent_page: Page) -> str:
    """Ensure a parent page has a children folder on Drive.

    If the parent already has children_folder_id, returns it.
    Otherwise acquires a row lock to prevent duplicates, creates the
    folder on Drive (after releasing the lock to avoid holding it during
    a slow network call), then writes the result back.
    """
    if parent_page.children_folder_id:
        return parent_page.children_folder_id

    # Acquire row lock to serialize concurrent callers
    result = await db.execute(
        select(Page).where(Page.id == parent_page.id).with_for_update()
    )
    locked_parent = result.scalar_one()

    # Double-check: another request may have created the folder while we waited
    if locked_parent.children_folder_id:
        # Release the lock by committing (no changes)
        await db.commit()
        return locked_parent.children_folder_id

    # Capture values we need, then release the lock before the slow Drive call
    parent_title = locked_parent.title
    parent_folder_id = locked_parent.drive_folder_id
    parent_id = locked_parent.id
    await db.commit()  # releases FOR UPDATE lock

    def _create_folder_sync() -> str:
        service = get_sa_drive_service()
        folder_metadata = {
            "name": parent_title,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_folder_id],
        }
        return service.files().create(
            body=folder_metadata, fields="id", supportsAllDrives=True,
        ).execute()["id"]

    folder_id = await asyncio.to_thread(_create_folder_sync)

    # Write back — re-fetch to avoid stale reference after the commit above
    result = await db.execute(select(Page).where(Page.id == parent_id))
    page = result.scalar_one()
    # Another caller may have set it while we were calling Drive — use theirs
    # and trash the orphan we just created
    if page.children_folder_id:
        try:
            from app.drive import trash_drive_file
            await asyncio.to_thread(trash_drive_file, None, folder_id)
        except Exception:
            pass  # Best-effort cleanup of orphan
        return page.children_folder_id
    page.children_folder_id = folder_id
    await db.commit()
    return folder_id
