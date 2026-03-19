from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.pages import schemas, service
from app.pages.schemas import _UNSET

router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("/tree", response_model=list[schemas.PageTreeNode])
async def get_page_tree(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.PageTreeNode]:
    return await service.get_page_tree(db, user)


@router.post(
    "",
    response_model=schemas.PageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_page(
    data: schemas.PageCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    page = await service.create_page(db, user, data)
    background_tasks.add_task(
        service.finalize_page_creation,
        page_id=page.id,
        user_email=user.email,
        title=data.title,
        is_private=page.is_private,
        target_folder_id=page.drive_folder_id,
    )
    return schemas.PageResponse.model_validate(page)


@router.post("/sync")
async def sync_pages(
    force: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    # Sync is scoped to user's visible pages (via visible_pages_filter in sync.py).
    # All authenticated users are @sarj.ai (enforced at login), so no extra guard needed.
    return await service.sync_drive_state(db, user, force=force)


@router.get("/{page_id}/content")
async def get_page_content(
    page_id: UUID,
    force_refresh: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await service.get_page_content(db, user, page_id, force_refresh=force_refresh)


@router.get("/{page_id}", response_model=schemas.PageResponse)
async def get_page(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
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
    from app.drive import rename_child_folder, rename_drive_file

    old_page = await service.get_page(db, user, page_id)
    old_title = old_page.title
    old_parent_id = old_page.parent_id

    page = await service.update_page(db, user, page_id, data)

    if data.title is not None and old_title != data.title:
        background_tasks.add_task(rename_drive_file, user, page.google_drive_id, data.title)
        background_tasks.add_task(rename_child_folder, user, old_title, data.title, page.drive_folder_id)

    if data.parent_id is not _UNSET and data.parent_id != old_parent_id:
        background_tasks.add_task(service.move_page_in_drive, user, page, data.parent_id)

    return schemas.PageResponse.model_validate(page)


@router.post("/{page_id}/retry", response_model=schemas.PageResponse)
async def retry_page_creation(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    page = await service.retry_pending_page(db, user, page_id)
    return schemas.PageResponse.model_validate(page)


@router.delete("/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_page(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await service.delete_page(db, user, page_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{page_id}/publish", response_model=schemas.PageResponse)
async def publish_page(
    page_id: UUID,
    data: schemas.PublishRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    page = await service.publish_page(db, user, page_id, data)
    return schemas.PageResponse.model_validate(page)
