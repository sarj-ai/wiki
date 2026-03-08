from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.database import async_session, get_db
from app.models.page import Page
from app.models.user import User
from app.pages import schemas, service

router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("/tree", response_model=list[schemas.PageTreeNode])
async def get_page_tree(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.PageTreeNode]:
    """Return the full page tree visible to the current user."""
    return await service.get_page_tree(db, user)


@router.post(
    "",
    response_model=schemas.PageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_page(
    data: schemas.PageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    """Create a new page."""
    page = await service.create_page(db, user, data)
    return schemas.PageResponse.model_validate(page)


@router.post("/sync")
async def sync_pages(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """Sync pages with Google Drive — remove pages whose files were trashed/deleted."""
    removed = await service.sync_drive_state(db, user)
    return {"removed": removed}


@router.get("/{page_id}/content")
async def get_page_content(
    page_id: UUID,
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Fetch the rendered content of a page's Google Doc.

    Returns simplified content blocks for native rendering.
    Uses caching -- content is only re-fetched from Google when the cache is stale.
    """
    from datetime import datetime, timezone

    from app.docs_transformer import enrich_chips_from_html, transform_doc
    from app.drive import export_doc_html, get_doc_content

    # First, verify access
    page = await service.get_page(db, user, page_id)

    # Check cache (cache is valid if content exists and wasn't invalidated)
    if not force_refresh and page.cached_content is not None and page.content_cached_at is not None:
        return {"blocks": page.cached_content, "cached": True}

    # Fetch from Google Docs API
    raw_doc = get_doc_content(user, page.google_drive_id)
    blocks = transform_doc(raw_doc)

    # If any tables have smart chips, fetch the HTML export to get real values.
    # The HTML export renders chips as actual text (the Docs API doesn't).
    has_chips = any(b.get("type") == "table" and b.get("hasChips") for b in blocks)
    if has_chips:
        try:
            html = export_doc_html(user, page.google_drive_id)
            blocks = enrich_chips_from_html(blocks, html)
        except Exception:
            pass  # Gracefully fall back to placeholder if export fails

    # Cache the result
    page.cached_content = blocks
    page.content_cached_at = datetime.now(timezone.utc)
    await db.commit()

    return {"blocks": blocks, "cached": False}


@router.get("/{page_id}", response_model=schemas.PageResponse)
async def get_page(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    """Fetch a single page by ID, enforcing access control."""
    page = await service.get_page(db, user, page_id)
    return schemas.PageResponse.model_validate(page)


@router.patch("/{page_id}", response_model=schemas.PageResponse)
async def update_page(
    page_id: UUID,
    data: schemas.PageUpdate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    """Update an existing page (rename, reorder, re-parent, change icon/cover)."""
    from app.drive import create_folder, move_drive_file, rename_child_folder, rename_drive_file
    from app.pages.schemas import _UNSET

    # Snapshot state before update for Drive sync
    old_page = await db.get(Page, page_id)
    old_title = old_page.title if old_page else None
    old_parent_id = old_page.parent_id if old_page else None
    old_drive_folder_id = old_page.drive_folder_id if old_page else None

    page = await service.update_page(db, user, page_id, data)

    # Sync rename to Drive in background
    if data.title is not None:
        background_tasks.add_task(rename_drive_file, user, page.google_drive_id, data.title)
        if old_title and old_title != data.title:
            background_tasks.add_task(rename_child_folder, user, old_title, data.title, page.drive_folder_id)

    # Sync re-parent to Drive in background
    if data.parent_id is not _UNSET and data.parent_id != old_parent_id:
        async def move_in_drive():
            try:
                if data.parent_id is not None:
                    # Moving under a new parent — get/create the parent's children folder
                    async with async_session() as move_db:
                        result = await move_db.execute(
                            select(Page).where(Page.id == data.parent_id)
                        )
                        new_parent = result.scalar_one_or_none()
                        if new_parent:
                            if new_parent.children_folder_id:
                                target = new_parent.children_folder_id
                            else:
                                target = create_folder(user, new_parent.title, new_parent.drive_folder_id)
                                new_parent.children_folder_id = target
                                await move_db.commit()
                            move_drive_file(user, page.google_drive_id, target)
                            # Update the page's drive_folder_id
                            async with async_session() as update_db:
                                result2 = await update_db.execute(
                                    select(Page).where(Page.id == page.id)
                                )
                                p = result2.scalar_one_or_none()
                                if p:
                                    p.drive_folder_id = target
                                    await update_db.commit()
                else:
                    # Moving to root — determine root folder
                    from app.config import settings
                    if page.is_private:
                        from app.drive import _get_drive_service, _ensure_folder
                        svc = _get_drive_service(user)
                        private_root = _ensure_folder(svc, "Private", settings.DRIVE_ROOT_FOLDER_ID)
                        target = _ensure_folder(svc, user.email, private_root)
                    else:
                        target = settings.DRIVE_ROOT_FOLDER_ID
                    move_drive_file(user, page.google_drive_id, target)
                    async with async_session() as update_db:
                        result2 = await update_db.execute(
                            select(Page).where(Page.id == page.id)
                        )
                        p = result2.scalar_one_or_none()
                        if p:
                            p.drive_folder_id = target
                            await update_db.commit()
            except Exception:
                pass  # Drive sync is best-effort

        background_tasks.add_task(move_in_drive)

    return schemas.PageResponse.model_validate(page)


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_page(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a page and all its children."""
    await service.delete_page(db, user, page_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{page_id}/publish", response_model=schemas.PageResponse)
async def publish_page(
    page_id: UUID,
    data: schemas.PublishRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    """Publish a private page to a workspace."""
    page = await service.publish_page(db, user, page_id, data)
    return schemas.PageResponse.model_validate(page)
