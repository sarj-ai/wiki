from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.workspace import Workspace
from app.permissions import schemas, service
from app.models.page import PagePermission

router = APIRouter(prefix="/permissions", tags=["permissions"])


async def _enrich_permission(db: AsyncSession, perm: PagePermission) -> dict:
    """Build a PermissionResponse-compatible dict with display info."""
    enriched = {
        "id": perm.id,
        "page_id": perm.page_id,
        "user_id": perm.user_id,
        "workspace_id": perm.workspace_id,
        "permission": perm.permission,
        "display_name": None,
        "email": None,
        "avatar_url": None,
    }
    if perm.user_id is not None:
        result = await db.execute(select(User).where(User.id == perm.user_id))
        u = result.scalar_one_or_none()
        if u:
            enriched["display_name"] = u.display_name
            enriched["email"] = u.email
            enriched["avatar_url"] = u.avatar_url
    elif perm.workspace_id is not None:
        result = await db.execute(select(Workspace).where(Workspace.id == perm.workspace_id))
        ws = result.scalar_one_or_none()
        if ws:
            enriched["display_name"] = ws.name
            enriched["email"] = ws.google_group_email
    return enriched


@router.get("/page/{page_id}", response_model=list[schemas.PermissionResponse])
async def list_permissions(
    page_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.PermissionResponse]:
    """List all permissions for a page."""
    return await service.list_permissions(db, user, page_id)


@router.get("/users/search", response_model=list[schemas.UserSearchResult])
async def search_users(
    q: str = Query(..., min_length=1, max_length=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.UserSearchResult]:
    """Search users by name or email for sharing."""
    results = await service.search_users(db, q, exclude_user_id=user.id)
    return [schemas.UserSearchResult.model_validate(u) for u in results]


@router.get("/workspaces/search", response_model=list[schemas.WorkspaceSearchResult])
async def search_workspaces(
    q: str = Query(..., min_length=1, max_length=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.WorkspaceSearchResult]:
    """Search workspaces by name or group email for sharing."""
    results = await service.search_workspaces(db, q, user_id=user.id)
    return [schemas.WorkspaceSearchResult.model_validate(ws) for ws in results]


@router.post(
    "",
    response_model=schemas.PermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_permission(
    data: schemas.PermissionCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PermissionResponse:
    """Grant permission on a page."""
    perm = await service.grant_permission(db, user, data)
    enriched = await _enrich_permission(db, perm)
    return schemas.PermissionResponse(**enriched)


@router.patch("/{permission_id}", response_model=schemas.PermissionResponse)
async def update_permission(
    permission_id: UUID,
    data: schemas.PermissionUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> schemas.PermissionResponse:
    """Update a permission's level (view / edit)."""
    perm = await service.update_permission(db, user, permission_id, data.permission)
    enriched = await _enrich_permission(db, perm)
    return schemas.PermissionResponse(**enriched)


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_permission(
    permission_id: UUID,
    recursive: bool = Query(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke a permission. Pass ?recursive=true to also remove from descendants."""
    await service.revoke_permission(db, user, permission_id, recursive=recursive)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
