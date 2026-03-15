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
    sort_order: float = 0


class WorkspaceUpdate(BaseModel):
    icon: str | None = None


class WorkspaceReorder(BaseModel):
    workspace_ids: list[str]  # ordered list of workspace IDs


async def _get_user_settings(db: AsyncSession, user_id) -> dict[str, WorkspaceUserSettings]:
    """Load per-user workspace settings as a {workspace_id: settings} map."""
    result = await db.execute(
        select(WorkspaceUserSettings).where(WorkspaceUserSettings.user_id == user_id)
    )
    return {str(s.workspace_id): s for s in result.scalars().all()}


def _to_out(ws: Workspace, settings: dict[str, WorkspaceUserSettings]) -> WorkspaceOut:
    ws_id = str(ws.id)
    s = settings.get(ws_id)
    return WorkspaceOut(
        id=ws_id,
        name=ws.name,
        google_group_email=ws.google_group_email,
        drive_folder_id=ws.drive_folder_id,
        icon=s.icon if s else None,
        sort_order=s.sort_order if s else 0,
    )


@router.get("", response_model=list[WorkspaceOut])
async def list_my_workspaces(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    """List workspaces the current user is a member of, sorted by user preference."""
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == current_user.id)
    )
    workspaces = result.scalars().all()
    settings = await _get_user_settings(db, current_user.id)
    out = [_to_out(ws, settings) for ws in workspaces]
    out.sort(key=lambda w: w.sort_order)
    return out


@router.post("/sync", response_model=list[WorkspaceOut])
async def sync_workspaces(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    """Sync all Google Groups as workspaces (admin action) and refresh user memberships."""
    all_workspaces = await sync_all_groups(db)
    await sync_folder_permissions(db)
    my_workspaces = await sync_user_memberships(db, current_user)
    settings = await _get_user_settings(db, current_user.id)
    out = [_to_out(ws, settings) for ws in my_workspaces]
    out.sort(key=lambda w: w.sort_order)
    return out


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

    settings = await _get_user_settings(db, current_user.id)
    return _to_out(ws, settings)


@router.put("/reorder", response_model=list[WorkspaceOut])
async def reorder_workspaces(
    data: WorkspaceReorder,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    """Persist the user's preferred workspace order."""
    settings = await _get_user_settings(db, current_user.id)

    for i, ws_id in enumerate(data.workspace_ids):
        if ws_id in settings:
            settings[ws_id].sort_order = float(i)
        else:
            db.add(WorkspaceUserSettings(
                user_id=current_user.id,
                workspace_id=ws_id,
                sort_order=float(i),
            ))

    await db.commit()

    # Re-fetch and return the sorted list
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
        .where(WorkspaceMembership.user_id == current_user.id)
    )
    workspaces = result.scalars().all()
    settings = await _get_user_settings(db, current_user.id)
    out = [_to_out(ws, settings) for ws in workspaces]
    out.sort(key=lambda w: w.sort_order)
    return out
