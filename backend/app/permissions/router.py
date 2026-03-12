from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.permissions import schemas, service

router = APIRouter(prefix="/permissions", tags=["permissions"])


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
    # Enrich the response with display_name/email
    enriched = {
        "id": perm.id,
        "page_id": perm.page_id,
        "user_id": perm.user_id,
        "workspace_id": perm.workspace_id,
        "permission": perm.permission,
        "display_name": None,
        "email": None,
    }
    if perm.user_id is not None:
        from sqlalchemy import select as sa_select
        from app.models.user import User as UserModel
        u_result = await db.execute(sa_select(UserModel).where(UserModel.id == perm.user_id))
        u_obj = u_result.scalar_one_or_none()
        if u_obj:
            enriched["display_name"] = u_obj.display_name
            enriched["email"] = u_obj.email
    return schemas.PermissionResponse(**enriched)


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_permission(
    permission_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke a permission."""
    await service.revoke_permission(db, user, permission_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
