from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.config import settings
from app.database import get_db
from app.models.page import Page, StarredPage
from app.models.user import User
from app.models.workspace import Workspace
from app.pages import schemas, service
from app.pages.schemas import _UNSET

router = APIRouter(prefix="/pages", tags=["pages"])


async def _enrich_page_response(db: AsyncSession, page: Page, user: User) -> schemas.PageResponse:
    """Build a PageResponse with denormalized creator/editor info and user permission."""
    from app.models.page import PagePermission
    from app.models.workspace import WorkspaceMembership
    from sqlalchemy import or_

    resp = schemas.PageResponse.model_validate(page)
    if page.created_by:
        result = await db.execute(select(User).where(User.id == page.created_by))
        creator = result.scalar_one_or_none()
        if creator:
            resp.created_by_name = creator.display_name
            resp.created_by_email = creator.email
            resp.created_by_avatar = creator.avatar_url
    if page.last_edited_by:
        result = await db.execute(select(User).where(User.id == page.last_edited_by))
        editor = result.scalar_one_or_none()
        if editor:
            resp.last_edited_by_email = editor.email
            resp.last_edited_by_name = editor.display_name

    # Compute the current user's effective permission
    if page.created_by == user.id:
        resp.current_user_permission = "owner"
    elif not page.is_private and page.workspace_id:
        wm = await db.execute(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.workspace_id == page.workspace_id,
            )
        )
        if wm.scalar_one_or_none() is not None:
            resp.current_user_permission = "edit"
    if resp.current_user_permission is None:
        # Check explicit permission (direct or via workspace)
        user_ws_ids = select(WorkspaceMembership.workspace_id).where(WorkspaceMembership.user_id == user.id)
        perm_result = await db.execute(
            select(PagePermission.permission).where(
                PagePermission.page_id == page.id,
                or_(
                    PagePermission.user_id == user.id,
                    PagePermission.workspace_id.in_(user_ws_ids),
                ),
            ).order_by(
                # Prefer "edit" over "view"
                PagePermission.permission.asc()
            ).limit(1)
        )
        perm_row = perm_result.scalar_one_or_none()
        if perm_row:
            resp.current_user_permission = perm_row

    return resp


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
    return await _enrich_page_response(db, page, user)


@router.post("/sync")
async def sync_pages(
    force: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    # Sync is scoped to user's visible pages (via visible_pages_filter in sync.py).
    # All authenticated users are @sarj.ai (enforced at login), so no extra guard needed.
    return await service.sync_drive_state(db, user, force=force)


@router.get("/starred")
async def get_starred_pages(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    result = await db.execute(
        select(StarredPage.page_id).where(StarredPage.user_id == user.id)
    )
    return [str(row[0]) for row in result.all()]


@router.post("/{page_id}/star", status_code=status.HTTP_201_CREATED)
async def star_page(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    # Verify access
    from app.pages.crud import get_page

    await get_page(db, user, page_id)
    existing = await db.execute(
        select(StarredPage).where(
            StarredPage.user_id == user.id, StarredPage.page_id == page_id
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(StarredPage(user_id=user.id, page_id=page_id))
        await db.commit()
    return Response(status_code=status.HTTP_201_CREATED)


@router.delete("/{page_id}/star", status_code=status.HTTP_204_NO_CONTENT)
async def unstar_page(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    await db.execute(
        sa_delete(StarredPage).where(
            StarredPage.user_id == user.id, StarredPage.page_id == page_id
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{page_id}/duplicate", response_model=schemas.PageResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_page(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    page = await service.duplicate_page(db, user, page_id)
    return await _enrich_page_response(db, page, user)


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
    return await _enrich_page_response(db, page, user)


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

    return await _enrich_page_response(db, page, user)


@router.post("/{page_id}/retry", response_model=schemas.PageResponse)
async def retry_page_creation(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    page = await service.retry_pending_page(db, user, page_id)
    return await _enrich_page_response(db, page, user)


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
    return await _enrich_page_response(db, page, user)


@router.post("/{page_id}/move-workspace", response_model=schemas.PageResponse)
async def move_to_workspace(
    page_id: UUID,
    data: schemas.MoveToWorkspaceRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PageResponse:
    page = await service.move_to_workspace(db, user, page_id, data.workspace_id)
    return await _enrich_page_response(db, page, user)
