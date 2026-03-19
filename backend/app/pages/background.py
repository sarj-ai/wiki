"""Background tasks for page operations (Drive doc creation, move)."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import assert_page_owner
from app.config import settings
from app.drive import (
    create_folder,
    create_google_doc,
    ensure_folder,
    get_sa_drive_service,
    move_drive_file,
)
from app.models.page import Page
from app.models.user import User
from app.models.workspace import Workspace
from app.pages.helpers import _ensure_children_folder, _get_page_or_403

log = logging.getLogger(__name__)


async def finalize_page_creation(page_id: UUID, user_email: str, title: str, is_private: bool, target_folder_id: str) -> None:
    """Background task: create the Google Doc on Drive and update the page record."""
    from app.database import async_session

    try:
        google_drive_id, drive_folder_id = await asyncio.to_thread(
            create_google_doc,
            user_email,
            title,
            is_private=is_private,
            target_folder_id=target_folder_id,
        )

        async with async_session() as db:
            result = await db.execute(select(Page).where(Page.id == page_id))
            page = result.scalar_one_or_none()
            if page is None:
                return
            page.google_drive_id = google_drive_id
            page.drive_folder_id = drive_folder_id
            await db.commit()

            from app.search.service import update_search_vector
            try:
                await update_search_vector(db, page)
                await db.commit()
            except Exception:
                await db.rollback()
                log.warning("Failed to update search vector for page %s", page_id)

        log.info("Background: created Drive doc for page %s -> %s", page_id, google_drive_id)
    except Exception:
        log.exception("Background: Drive doc creation failed for page %s — user can retry or delete", page_id)


async def retry_pending_page(
    db: AsyncSession,
    user: User,
    page_id: UUID,
) -> Page:
    """Retry Drive doc creation for a stuck pending page.

    Re-resolves the correct folder from the DB tree, creating the
    children folder on Drive if it doesn't exist yet.
    """
    page = await _get_page_or_403(db, page_id)
    assert_page_owner(page, user)

    if not page.google_drive_id.startswith("pending-"):
        return page  # Already created, nothing to do

    # Re-resolve target folder from the DB tree
    target_folder_id: str | None = None

    if page.parent_id is not None:
        parent_page = await _get_page_or_403(db, page.parent_id)
        target_folder_id = await _ensure_children_folder(db, parent_page)
    elif page.workspace_id is not None:
        ws_result = await db.execute(
            select(Workspace).where(Workspace.id == page.workspace_id)
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
            status_code=400,
            detail="Cannot determine Drive folder for this page",
        )

    try:
        google_drive_id, drive_folder_id = await asyncio.to_thread(
            create_google_doc,
            user.email,
            page.title,
            is_private=page.is_private,
            target_folder_id=target_folder_id,
        )
    except Exception:
        log.exception("Retry: Drive doc creation failed for page %s", page_id)
        raise HTTPException(
            status_code=502,
            detail="Failed to create document on Drive, please try again",
        )

    page.google_drive_id = google_drive_id
    page.drive_folder_id = drive_folder_id
    await db.commit()
    await db.refresh(page)

    log.info("Retry: created Drive doc for page %s -> %s", page_id, google_drive_id)

    from app.search.service import update_search_vector
    try:
        await update_search_vector(db, page)
        await db.commit()
    except Exception:
        await db.rollback()
        log.warning("Failed to update search vector for page %s", page.id)

    return page


async def move_page_in_drive(
    user: User,
    page: Page,
    new_parent_id: UUID | None,
) -> None:
    """Move a page's Drive file to match a re-parent operation.

    This is designed to run as a background task. Failures are logged
    but do not propagate (Drive sync is best-effort).

    The incoming ``page`` object may be detached from its original session,
    so we re-fetch it from the DB to guarantee fresh data.
    """
    from app.database import async_session

    # Re-fetch the page to avoid stale/detached object issues
    async with async_session() as db:
        result = await db.execute(select(Page).where(Page.id == page.id))
        current_page = result.scalar_one_or_none()
    if not current_page:
        return

    # Skip if Drive doc hasn't been created yet
    if current_page.google_drive_id.startswith("pending-"):
        return

    async def _update_page_folder(page_id: UUID, folder_id: str) -> None:
        async with async_session() as db:
            result = await db.execute(select(Page).where(Page.id == page_id))
            p = result.scalar_one_or_none()
            if p:
                p.drive_folder_id = folder_id
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
                    target_folder_id = await asyncio.to_thread(
                        create_folder, user, new_parent.title, new_parent.drive_folder_id,
                    )
                    new_parent.children_folder_id = target_folder_id
                    await db.commit()

                await asyncio.to_thread(move_drive_file, user, current_page.google_drive_id, target_folder_id)
                await _update_page_folder(current_page.id, target_folder_id)
        else:
            if current_page.is_private:
                def _resolve_private_folder() -> str:
                    svc = get_sa_drive_service()
                    private_root = ensure_folder(svc, "My Drafts", settings.DRIVE_ROOT_FOLDER_ID)
                    return ensure_folder(svc, user.email, private_root)

                target_folder_id = await asyncio.to_thread(_resolve_private_folder)
            else:
                target_folder_id = settings.DRIVE_ROOT_FOLDER_ID

            await asyncio.to_thread(move_drive_file, user, current_page.google_drive_id, target_folder_id)
            await _update_page_folder(current_page.id, target_folder_id)
    except Exception:
        log.exception(
            "Failed to move Drive file %s to parent %s",
            current_page.google_drive_id,
            new_parent_id,
        )
