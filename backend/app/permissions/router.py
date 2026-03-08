from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
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
    return schemas.PermissionResponse.model_validate(perm)


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_permission(
    permission_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Revoke a permission."""
    await service.revoke_permission(db, user, permission_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
