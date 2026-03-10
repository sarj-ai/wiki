"""Workspace sync — fetch Google Groups via Admin SDK (service account)
and sync user memberships on login (user's own token via Cloud Identity)."""

from __future__ import annotations

import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.drive import build_credentials, ensure_folder
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership

log = logging.getLogger(__name__)

# Scopes for the service account (read-only directory access)
_SA_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
]


def _get_admin_service():
    """Build an Admin SDK Directory service using the service account
    with domain-wide delegation (impersonating the admin email)."""
    credentials = service_account.Credentials.from_service_account_file(
        settings.SERVICE_ACCOUNT_KEY_PATH,
        scopes=_SA_SCOPES,
    )
    delegated = credentials.with_subject(settings.ADMIN_IMPERSONATE_EMAIL)
    return build("admin", "directory_v1", credentials=delegated, static_discovery=True)


async def sync_all_groups(db: AsyncSession) -> list[Workspace]:
    """Fetch all groups from Google Admin SDK and upsert into workspaces table.

    Also creates a matching Drive folder under the root for each workspace.
    Returns the list of upserted workspaces.
    """
    service = _get_admin_service()

    groups: list[dict] = []
    page_token = None

    while True:
        result = service.groups().list(
            domain=settings.ALLOWED_DOMAIN,
            pageToken=page_token,
            maxResults=200,
        ).execute()

        groups.extend(result.get("groups", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    log.info("Fetched %d groups from Google Admin SDK", len(groups))

    upserted: list[Workspace] = []
    for group in groups:
        group_email = group["email"]
        group_name = group.get("name", group_email.split("@")[0])

        # Check if workspace already exists
        result = await db.execute(
            select(Workspace).where(Workspace.google_group_email == group_email)
        )
        workspace = result.scalar_one_or_none()

        if workspace is None:
            # Create Drive folder for this workspace
            drive_folder_id = _create_workspace_drive_folder(group_name)

            workspace = Workspace(
                name=group_name,
                google_group_email=group_email,
                drive_folder_id=drive_folder_id,
            )
            db.add(workspace)
            log.info("Created workspace: %s (%s)", group_name, group_email)
        else:
            # Update name if changed
            if workspace.name != group_name:
                workspace.name = group_name
                log.info("Updated workspace name: %s", group_name)

        upserted.append(workspace)

    await db.commit()
    return upserted


def _create_workspace_drive_folder(workspace_name: str) -> str:
    """Create a Drive folder for a workspace under the Sarj Docs root.

    Uses the service account's own credentials (not user impersonation)
    to create the folder in the shared drive.
    """
    credentials = service_account.Credentials.from_service_account_file(
        settings.SERVICE_ACCOUNT_KEY_PATH,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    drive_service = build("drive", "v3", credentials=credentials, static_discovery=True)
    return ensure_folder(drive_service, workspace_name, settings.DRIVE_ROOT_FOLDER_ID)


async def sync_user_memberships(db: AsyncSession, user: User) -> list[Workspace]:
    """Sync a user's group memberships using their own OAuth token.

    Fetches groups the user belongs to via the Cloud Identity API,
    matches them against existing workspaces, and upserts memberships.
    Returns the list of workspaces the user is a member of.
    """
    credentials = build_credentials(user)
    # Use the People API / Directory API with user's own token to get their groups
    # The user can see their own group memberships
    service = build("admin", "directory_v1", credentials=credentials, static_discovery=True)

    user_groups: list[dict] = []
    page_token = None

    try:
        while True:
            result = service.groups().list(
                userKey=user.email,
                pageToken=page_token,
                maxResults=200,
            ).execute()

            user_groups.extend(result.get("groups", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        log.warning(
            "Could not fetch groups for %s via user token, falling back to service account",
            user.email,
        )
        # Fallback: use service account to check this user's memberships
        user_groups = _get_user_groups_via_service_account(user.email)

    log.info("User %s belongs to %d groups", user.email, len(user_groups))

    # Match groups to existing workspaces and upsert memberships
    group_emails = {g["email"] for g in user_groups}
    result = await db.execute(
        select(Workspace).where(Workspace.google_group_email.in_(group_emails))
    )
    matched_workspaces = list(result.scalars().all())

    # Get existing memberships for this user
    existing = await db.execute(
        select(WorkspaceMembership.workspace_id).where(
            WorkspaceMembership.user_id == user.id
        )
    )
    existing_workspace_ids = {row[0] for row in existing.all()}

    # Add missing memberships
    for ws in matched_workspaces:
        if ws.id not in existing_workspace_ids:
            db.add(WorkspaceMembership(user_id=user.id, workspace_id=ws.id))
            log.info("Added %s to workspace %s", user.email, ws.name)

    # Remove stale memberships (user left a group)
    matched_ids = {ws.id for ws in matched_workspaces}
    stale_ids = existing_workspace_ids - matched_ids
    if stale_ids:
        from sqlalchemy import and_, delete

        await db.execute(
            delete(WorkspaceMembership).where(
                and_(
                    WorkspaceMembership.user_id == user.id,
                    WorkspaceMembership.workspace_id.in_(stale_ids),
                )
            )
        )
        log.info("Removed %s from %d stale workspaces", user.email, len(stale_ids))

    await db.commit()
    return matched_workspaces


def _get_user_groups_via_service_account(email: str) -> list[dict]:
    """Fallback: fetch a user's groups using the service account."""
    service = _get_admin_service()
    groups: list[dict] = []
    page_token = None

    while True:
        result = service.groups().list(
            userKey=email,
            pageToken=page_token,
            maxResults=200,
        ).execute()

        groups.extend(result.get("groups", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return groups
