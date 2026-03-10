"""Workspace API endpoints."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership
from app.workspaces.service import sync_all_groups, sync_user_memberships

log = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceOut(BaseModel):
    id: str
    name: str
    google_group_email: str
    drive_folder_id: str


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
    return [
        WorkspaceOut(
            id=str(ws.id),
            name=ws.name,
            google_group_email=ws.google_group_email,
            drive_folder_id=ws.drive_folder_id,
        )
        for ws in workspaces
    ]


@router.post("/sync", response_model=list[WorkspaceOut])
async def sync_workspaces(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    """Sync all Google Groups as workspaces (admin action) and refresh user memberships."""
    # Step 1: Sync all groups from Admin SDK
    all_workspaces = await sync_all_groups(db)

    # Step 2: Sync this user's memberships
    my_workspaces = await sync_user_memberships(db, current_user)

    return [
        WorkspaceOut(
            id=str(ws.id),
            name=ws.name,
            google_group_email=ws.google_group_email,
            drive_folder_id=ws.drive_folder_id,
        )
        for ws in my_workspaces
    ]
