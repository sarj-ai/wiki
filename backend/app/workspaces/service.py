"""Workspace sync — fetch Google Groups via Admin SDK (service account),
scan Drive folder permissions for group access, and sync user memberships."""

from __future__ import annotations

import asyncio
import logging

from google.oauth2 import service_account
from googleapiclient.discovery import build
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.drive import build_credentials, ensure_folder, get_sa_drive_service
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceGroupAccess, WorkspaceMembership

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


def _get_all_domain_groups() -> set[str]:
    """Fetch all group emails in the domain from Admin SDK."""
    service = _get_admin_service()
    group_emails: set[str] = set()
    page_token = None

    while True:
        result = service.groups().list(
            domain=settings.ALLOWED_DOMAIN,
            pageToken=page_token,
            maxResults=200,
        ).execute()

        for g in result.get("groups", []):
            group_emails.add(g["email"])
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return group_emails


def _fetch_all_groups_sync() -> list[dict]:
    """Fetch all groups from Admin SDK synchronously (for use in a thread)."""
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
    return groups


async def sync_all_groups(db: AsyncSession) -> list[Workspace]:
    """Fetch all groups from Google Admin SDK and upsert into workspaces table.

    Also creates a matching Drive folder under the root for each workspace.
    Returns the list of upserted workspaces.
    """
    groups = await asyncio.to_thread(_fetch_all_groups_sync)

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
            drive_folder_id = await asyncio.to_thread(
                _create_workspace_drive_folder, group_name, group_email
            )

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


def _list_root_folders_sync() -> dict[str, str]:
    """List all live folders under the Drive root (synchronous, for threading)."""
    drive_service = get_sa_drive_service()
    live_folders: dict[str, str] = {}
    page_token = None
    while True:
        resp = drive_service.files().list(
            q=(
                f"'{settings.DRIVE_ROOT_FOLDER_ID}' in parents "
                f"and mimeType = 'application/vnd.google-apps.folder' "
                f"and trashed = false"
            ),
            fields="nextPageToken,files(id,name)",
            spaces="drive",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=200,
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            live_folders[f["id"]] = f["name"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return live_folders


def _list_folder_permissions_sync(folder_id: str) -> list[dict]:
    """List permissions for a Drive folder (synchronous, for threading)."""
    drive_service = get_sa_drive_service()
    perms_result = drive_service.permissions().list(
        fileId=folder_id,
        fields="permissions(emailAddress,type,role)",
        supportsAllDrives=True,
    ).execute()
    return perms_result.get("permissions", [])


async def sync_folder_permissions(db: AsyncSession) -> None:
    """Scan Drive folders and their permissions to:

    1. Remove workspaces whose Drive folders have been trashed or deleted
    2. Discover new top-level folders with group permissions and create workspaces
    3. Update workspace_group_access for cross-group sharing

    This means manually creating a folder in Drive and sharing it with a
    Google Group will automatically create a workspace.
    """
    # Get all domain groups to filter permissions
    domain_groups = await asyncio.to_thread(_get_all_domain_groups)
    log.info("Domain has %d groups for permission matching", len(domain_groups))

    # --- Phase 1: Remove workspaces whose Drive folders are gone ---
    result = await db.execute(select(Workspace))
    workspaces = list(result.scalars().all())

    # Collect all live (non-trashed) folders under root
    live_folders = await asyncio.to_thread(_list_root_folders_sync)

    async def _fetch_perms(folder_id: str) -> list[dict] | None:
        try:
            return await asyncio.to_thread(_list_folder_permissions_sync, folder_id)
        except Exception:
            return None

    # Delete workspaces whose folders are no longer live (only folder-discovered ones)
    for ws in workspaces:
        if ws.google_group_email is None and ws.drive_folder_id not in live_folders:
            log.info("Removing workspace '%s' — Drive folder deleted", ws.name)
            await db.execute(
                delete(WorkspaceMembership).where(WorkspaceMembership.workspace_id == ws.id)
            )
            await db.execute(
                delete(WorkspaceGroupAccess).where(WorkspaceGroupAccess.workspace_id == ws.id)
            )
            await db.delete(ws)

    await db.flush()

    # Re-fetch clean list after deletions
    result = await db.execute(select(Workspace))
    workspaces = list(result.scalars().all())
    known_folder_ids = {ws.drive_folder_id for ws in workspaces}

    # --- Phase 2: Discover new folders under Sarj Docs root ---
    new_folders = {
        fid: fname for fid, fname in live_folders.items()
        if fid not in known_folder_ids and fname != "My Drafts"
    }

    if new_folders:
        # Fetch permissions for all new folders in parallel
        new_folder_ids = list(new_folders.keys())
        new_perm_results = await asyncio.gather(
            *[_fetch_perms(fid) for fid in new_folder_ids]
        )

        for folder_id, permissions in zip(new_folder_ids, new_perm_results):
            if permissions is None:
                continue
            folder_name = new_folders[folder_id]

            try:
                folder_groups = set()
                for perm in permissions:
                    if perm.get("type") == "group":
                        email = perm.get("emailAddress", "")
                        if email in domain_groups:
                            folder_groups.add(email)

                if folder_groups:
                    ws = Workspace(
                        name=folder_name,
                        google_group_email=None,
                        drive_folder_id=folder_id,
                    )
                    db.add(ws)
                    await db.flush()
                    workspaces.append(ws)
                    known_folder_ids.add(folder_id)

                    for email in folder_groups:
                        db.add(WorkspaceGroupAccess(
                            workspace_id=ws.id,
                            google_group_email=email,
                        ))

                    log.info(
                        "Created workspace from Drive folder: %s (groups: %s)",
                        folder_name, ", ".join(folder_groups),
                    )
            except Exception:
                log.warning("Failed to check permissions for folder %s", folder_name, exc_info=True)

    await db.commit()

    # --- Phase 2: Update group access for existing workspaces ---
    # Re-fetch workspaces (may include newly created ones)
    result = await db.execute(select(Workspace))
    workspaces = list(result.scalars().all())

    # Fetch all folder permissions in parallel (Drive API calls only)
    perm_results = await asyncio.gather(
        *[_fetch_perms(ws.drive_folder_id) for ws in workspaces]
    )

    # Process results sequentially (DB operations)
    for ws, permissions in zip(workspaces, perm_results):
        if permissions is None:
            log.warning("Failed to scan permissions for workspace %s", ws.name)
            continue

        try:
            group_emails: set[str] = set()
            for perm in permissions:
                if perm.get("type") == "group":
                    email = perm.get("emailAddress", "")
                    if email in domain_groups:
                        group_emails.add(email)

            # Always include the primary group (if set)
            if ws.google_group_email:
                group_emails.add(ws.google_group_email)

            # Get current group access entries
            existing = await db.execute(
                select(WorkspaceGroupAccess.google_group_email).where(
                    WorkspaceGroupAccess.workspace_id == ws.id
                )
            )
            existing_emails = {row[0] for row in existing.all()}

            # Add new entries
            for email in group_emails - existing_emails:
                db.add(WorkspaceGroupAccess(
                    workspace_id=ws.id,
                    google_group_email=email,
                ))
                log.info("Added group access: %s -> %s", email, ws.name)

            # Remove stale entries
            stale = existing_emails - group_emails
            if stale:
                await db.execute(
                    delete(WorkspaceGroupAccess).where(
                        and_(
                            WorkspaceGroupAccess.workspace_id == ws.id,
                            WorkspaceGroupAccess.google_group_email.in_(stale),
                        )
                    )
                )
                log.info("Removed %d stale group access entries for %s", len(stale), ws.name)

        except Exception:
            log.warning("Failed to process permissions for workspace %s", ws.name, exc_info=True)

    await db.commit()
    log.info("Folder permission sync complete")


def _create_workspace_drive_folder(workspace_name: str, group_email: str) -> str:
    """Create a Drive folder for a workspace under the Sarj Docs root
    and grant the Google Group read/write access.

    Uses the service account's own credentials (not user impersonation)
    to create the folder in the shared drive.
    """
    drive_service = get_sa_drive_service()
    folder_id = ensure_folder(drive_service, workspace_name, settings.DRIVE_ROOT_FOLDER_ID)

    # Grant the Google Group editor access to the folder
    try:
        drive_service.permissions().create(
            fileId=folder_id,
            body={
                "type": "group",
                "role": "writer",
                "emailAddress": group_email,
            },
            supportsAllDrives=True,
            sendNotificationEmail=False,
        ).execute()
        log.info("Granted %s access to folder %s", group_email, folder_id)
    except Exception:
        log.warning("Failed to grant %s access to folder %s", group_email, folder_id)

    return folder_id


async def sync_user_memberships(db: AsyncSession, user: User) -> list[Workspace]:
    """Sync a user's group memberships.

    Matches user's groups against both:
    1. Primary workspace groups (workspaces.google_group_email)
    2. Additional group access (workspace_group_access table)

    This means if a workspace folder has been shared with extra groups,
    members of those groups will also get workspace access.
    """
    def _fetch_user_groups_with_user_token() -> list[dict]:
        credentials = build_credentials(user)
        service = build("admin", "directory_v1", credentials=credentials, static_discovery=True)
        groups: list[dict] = []
        page_token = None
        while True:
            result = service.groups().list(
                userKey=user.email,
                pageToken=page_token,
                maxResults=200,
            ).execute()
            groups.extend(result.get("groups", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return groups

    user_groups: list[dict] = []

    try:
        user_groups = await asyncio.to_thread(_fetch_user_groups_with_user_token)
    except Exception:
        log.warning(
            "Could not fetch groups for %s via user token, falling back to service account",
            user.email,
        )
        user_groups = await asyncio.to_thread(
            _get_user_groups_via_service_account, user.email
        )

    log.info("User %s belongs to %d groups", user.email, len(user_groups))

    user_group_emails = {g["email"] for g in user_groups}

    # Match via primary group email
    result = await db.execute(
        select(Workspace).where(Workspace.google_group_email.in_(user_group_emails))
    )
    primary_workspaces = list(result.scalars().all())

    # Match via workspace_group_access (folders shared with additional groups)
    result = await db.execute(
        select(WorkspaceGroupAccess.workspace_id).where(
            WorkspaceGroupAccess.google_group_email.in_(user_group_emails)
        )
    )
    extra_workspace_ids = {row[0] for row in result.all()}

    # Combine: primary + extra
    matched_ids = {ws.id for ws in primary_workspaces} | extra_workspace_ids

    # Fetch all matched workspace objects
    if matched_ids:
        result = await db.execute(
            select(Workspace).where(Workspace.id.in_(matched_ids))
        )
        matched_workspaces = list(result.scalars().all())
    else:
        matched_workspaces = []

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
    stale_ids = existing_workspace_ids - matched_ids
    if stale_ids:
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


async def sync_all_user_memberships(db: AsyncSession) -> None:
    """Sync workspace memberships for ALL users in the database.

    Uses the service account to fetch each user's groups (avoids
    needing user tokens).
    """
    result = await db.execute(select(User))
    users = list(result.scalars().all())
    log.info("Syncing memberships for %d users", len(users))

    for user in users:
        try:
            await sync_user_memberships(db, user)
        except Exception:
            log.warning("Failed to sync memberships for %s", user.email, exc_info=True)
