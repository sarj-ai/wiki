"""Workspace API endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership, WorkspaceUserSettings
from app.workspaces.service import sync_all_groups, sync_folder_permissions, sync_user_memberships

log = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceOut(BaseModel):
    id: str
    name: str
    google_group_email: str | None
    drive_folder_id: str
    icon: str | None = None


class WorkspaceUpdate(BaseModel):
    icon: str | None = None


async def _get_user_icons(db: AsyncSession, user_id) -> dict[str, str | None]:
    """Load per-user workspace icons as a {workspace_id: icon} map."""
    result = await db.execute(
        select(WorkspaceUserSettings).where(WorkspaceUserSettings.user_id == user_id)
    )
    return {str(s.workspace_id): s.icon for s in result.scalars().all()}


def _to_out(ws: Workspace, icons: dict[str, str | None]) -> WorkspaceOut:
    ws_id = str(ws.id)
    return WorkspaceOut(
        id=ws_id,
        name=ws.name,
        google_group_email=ws.google_group_email,
        drive_folder_id=ws.drive_folder_id,
        icon=icons.get(ws_id),
    )


@router.get("", response_model=list[WorkspaceOut])
async def list_my_workspaces(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    """List workspaces the current user is a member of."""
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == current_user.id)
    )
    workspaces = result.scalars().all()
    icons = await _get_user_icons(db, current_user.id)
    return [_to_out(ws, icons) for ws in workspaces]


@router.post("/sync", response_model=list[WorkspaceOut])
async def sync_workspaces(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    """Sync all Google Groups as workspaces (admin action) and refresh user memberships."""
    all_workspaces = await sync_all_groups(db)
    await sync_folder_permissions(db)
    my_workspaces = await sync_user_memberships(db, current_user)
    icons = await _get_user_icons(db, current_user.id)
    return [_to_out(ws, icons) for ws in my_workspaces]


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: str,
    data: WorkspaceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    """Update per-user workspace settings (e.g. icon). User must be a member."""
    # Verify membership
    membership = await db.execute(
        select(WorkspaceMembership).where(
            WorkspaceMembership.user_id == current_user.id,
            WorkspaceMembership.workspace_id == workspace_id,
        )
    )
    if not membership.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this workspace")

    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Upsert per-user settings
    if "icon" in data.model_fields_set:
        result = await db.execute(
            select(WorkspaceUserSettings).where(
                WorkspaceUserSettings.user_id == current_user.id,
                WorkspaceUserSettings.workspace_id == workspace_id,
            )
        )
        user_settings = result.scalar_one_or_none()

        if user_settings:
            user_settings.icon = data.icon
        else:
            user_settings = WorkspaceUserSettings(
                user_id=current_user.id,
                workspace_id=ws.id,
                icon=data.icon,
            )
            db.add(user_settings)

        await db.commit()

    icons = await _get_user_icons(db, current_user.id)
    return _to_out(ws, icons)
