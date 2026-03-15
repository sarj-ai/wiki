from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import assert_page_owner
from app.drive import get_sa_drive_service
from app.models.page import Page, PagePermission
from app.models.user import User
from app.models.workspace import Workspace
from app.permissions.schemas import PermissionCreate

log = logging.getLogger(__name__)

# Map our permission levels to Google Drive roles
_PERM_TO_DRIVE_ROLE: dict[str, str] = {
    "view": "reader",
    "edit": "writer",
}


# ------------------------------------------------------------------
# Drive sharing helpers
# ------------------------------------------------------------------

def _grant_drive_permission(
    file_id: str,
    email: str,
    role: str,
    *,
    perm_type: str = "user",
) -> str | None:
    """Grant a Drive permission (blocking). Returns the Drive permission ID."""
    service = get_sa_drive_service()
    result = service.permissions().create(
        fileId=file_id,
        body={
            "type": perm_type,
            "role": role,
            "emailAddress": email,
        },
        supportsAllDrives=True,
        sendNotificationEmail=False,
        fields="id",
    ).execute()
    return result.get("id")


def _revoke_drive_permission(file_id: str, email: str) -> None:
    """Find and delete a Drive permission by email (blocking)."""
    service = get_sa_drive_service()

    # List all permissions on the file to find the one matching the email
    resp = service.permissions().list(
        fileId=file_id,
        supportsAllDrives=True,
        fields="permissions(id,emailAddress,type)",
    ).execute()

    for perm in resp.get("permissions", []):
        if perm.get("emailAddress", "").lower() == email.lower():
            service.permissions().delete(
                fileId=file_id,
                permissionId=perm["id"],
                supportsAllDrives=True,
            ).execute()
            log.info(
                "Revoked Drive permission %s on %s for %s",
                perm["id"], file_id, email,
            )
            return

    log.warning(
        "No Drive permission found for %s on file %s — nothing to revoke",
        email, file_id,
    )


def _is_pending_drive_id(google_drive_id: str) -> bool:
    """Check whether the drive ID is a placeholder (doc not yet created)."""
    return google_drive_id.startswith("pending-")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _get_page_or_403(db: AsyncSession, page_id: UUID) -> Page:
    """Fetch a page by id or raise 403 (never 404 — don't leak existence)."""
    result = await db.execute(select(Page).where(Page.id == page_id))
    page = result.scalar_one_or_none()
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this page",
        )
    return page


async def _resolve_email_for_permission(
    db: AsyncSession,
    perm: PagePermission,
) -> tuple[str, str] | None:
    """Resolve the email and Drive permission type for a PagePermission.

    Returns (email, perm_type) or None if unresolvable.
    perm_type is "user" or "group".
    """
    if perm.user_id is not None:
        result = await db.execute(select(User).where(User.id == perm.user_id))
        user = result.scalar_one_or_none()
        if user is not None:
            return user.email, "user"
    if perm.workspace_id is not None:
        result = await db.execute(
            select(Workspace).where(Workspace.id == perm.workspace_id)
        )
        ws = result.scalar_one_or_none()
        if ws is not None and ws.google_group_email:
            return ws.google_group_email, "group"
    return None


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def list_permissions(
    db: AsyncSession,
    user: User,
    page_id: UUID,
) -> list[dict]:
    """List all permissions for a page. User must own the page."""
    page = await _get_page_or_403(db, page_id)
    assert_page_owner(page, user, action="manage permissions for")

    result = await db.execute(
        select(PagePermission).where(PagePermission.page_id == page_id)
    )
    perms = list(result.scalars().all())

    # Enrich with display_name and email
    enriched = []
    for perm in perms:
        row = {
            "id": perm.id,
            "page_id": perm.page_id,
            "user_id": perm.user_id,
            "workspace_id": perm.workspace_id,
            "permission": perm.permission,
            "display_name": None,
            "email": None,
        }
        if perm.user_id is not None:
            u = await db.execute(select(User).where(User.id == perm.user_id))
            u_obj = u.scalar_one_or_none()
            if u_obj:
                row["display_name"] = u_obj.display_name
                row["email"] = u_obj.email
        elif perm.workspace_id is not None:
            ws = await db.execute(
                select(Workspace).where(Workspace.id == perm.workspace_id)
            )
            ws_obj = ws.scalar_one_or_none()
            if ws_obj:
                row["display_name"] = ws_obj.name
                row["email"] = ws_obj.google_group_email
        enriched.append(row)
    return enriched


async def search_users(
    db: AsyncSession,
    query: str,
    *,
    exclude_user_id: UUID | None = None,
    limit: int = 10,
) -> list[User]:
    """Search users by email or display name.

    First checks the local DB.  If fewer than `limit` results, supplements
    with a Google Admin Directory search so that @sarj.ai members who
    haven't logged in yet are still discoverable.  Directory-only users are
    inserted into the DB (without tokens) so they can receive permissions.
    """
    pattern = f"%{query.lower()}%"
    stmt = select(User).where(
        (User.email.ilike(pattern)) | (User.display_name.ilike(pattern))
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    db_users = list(result.scalars().all())

    if len(db_users) >= limit:
        return db_users

    # Supplement from Google Admin Directory
    try:
        directory_users = await asyncio.to_thread(
            _search_directory, query, limit - len(db_users),
        )
    except Exception:
        log.warning("Admin Directory search failed", exc_info=True)
        return db_users

    # Merge: skip users already in DB results
    db_emails = {u.email.lower() for u in db_users}
    if exclude_user_id is not None:
        # Also exclude the requesting user by looking up their email
        result = await db.execute(select(User.email).where(User.id == exclude_user_id))
        row = result.scalar_one_or_none()
        if row:
            db_emails.add(row.lower())

    for du in directory_users:
        if du["email"].lower() in db_emails:
            continue
        # Check if user exists in DB but didn't match the ILIKE (edge case)
        existing = await db.execute(
            select(User).where(User.email == du["email"])
        )
        user_obj = existing.scalar_one_or_none()
        if user_obj is None:
            # Insert a stub user record (no tokens — they haven't logged in)
            user_obj = User(
                id=uuid4(),
                email=du["email"],
                google_id=du["google_id"],
                display_name=du.get("display_name"),
                avatar_url=du.get("avatar_url"),
            )
            db.add(user_obj)
            await db.flush()
            log.info("Created stub user from directory: %s", du["email"])
        db_users.append(user_obj)
        db_emails.add(du["email"].lower())
        if len(db_users) >= limit:
            break

    await db.commit()
    return db_users


def _search_directory(query: str, limit: int) -> list[dict]:
    """Search the Google Admin Directory for @sarj.ai users (blocking).

    Tries users.list first (requires admin.directory.user.readonly scope).
    Falls back to scanning group members (already-authorized scope) and
    filtering by the query string.

    Returns a list of dicts with keys: email, google_id, display_name, avatar_url.
    """
    import google_auth_httplib2
    import httplib2
    from google.oauth2 import service_account as sa
    from googleapiclient.discovery import build as api_build
    from app.config import settings as cfg

    # --- Attempt 1: Direct user search (needs user.readonly scope) ---
    try:
        credentials = sa.Credentials.from_service_account_file(
            cfg.SERVICE_ACCOUNT_KEY_PATH,
            scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
        )
        delegated = credentials.with_subject(cfg.ADMIN_IMPERSONATE_EMAIL)
        authed_http = google_auth_httplib2.AuthorizedHttp(
            delegated, http=httplib2.Http(timeout=5),
        )
        service = api_build(
            "admin", "directory_v1", http=authed_http, static_discovery=True,
        )

        resp = service.users().list(
            domain=cfg.ALLOWED_DOMAIN,
            query=query,
            maxResults=limit,
            projection="basic",
            fields="users(primaryEmail,id,name,thumbnailPhotoUrl)",
        ).execute()

        results: list[dict] = []
        for u in resp.get("users", []):
            full_name = u.get("name", {})
            results.append({
                "email": u["primaryEmail"],
                "google_id": u["id"],
                "display_name": full_name.get("fullName"),
                "avatar_url": u.get("thumbnailPhotoUrl"),
            })
        return results
    except Exception:
        log.info("Directory users.list unavailable, falling back to group member scan")

    # --- Attempt 2: Scan group members (uses already-authorized scopes) ---
    try:
        credentials = sa.Credentials.from_service_account_file(
            cfg.SERVICE_ACCOUNT_KEY_PATH,
            scopes=[
                "https://www.googleapis.com/auth/admin.directory.group.readonly",
                "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
            ],
        )
        delegated = credentials.with_subject(cfg.ADMIN_IMPERSONATE_EMAIL)
        authed_http = google_auth_httplib2.AuthorizedHttp(
            delegated, http=httplib2.Http(timeout=10),
        )
        service = api_build("admin", "directory_v1", http=authed_http, static_discovery=True)

        # Get all groups in the domain
        groups_resp = service.groups().list(
            domain=cfg.ALLOWED_DOMAIN, maxResults=200,
        ).execute()

        seen_emails: set[str] = set()
        results = []
        query_lower = query.lower()

        for group in groups_resp.get("groups", []):
            members_resp = service.members().list(
                groupKey=group["email"], maxResults=200,
            ).execute()

            for member in members_resp.get("members", []):
                email = member.get("email", "")
                if not email or email.lower() in seen_emails:
                    continue
                seen_emails.add(email.lower())

                # Filter by query
                if query_lower in email.lower():
                    results.append({
                        "email": email,
                        "google_id": member.get("id", email),
                        "display_name": None,
                        "avatar_url": None,
                    })
                    if len(results) >= limit:
                        return results

        return results
    except Exception:
        log.warning("Group member scan also failed", exc_info=True)
        return []


async def grant_permission(
    db: AsyncSession,
    user: User,
    data: PermissionCreate,
) -> PagePermission:
    """Grant permission on a page. Only the page owner can grant permissions."""
    page = await _get_page_or_403(db, data.page_id)
    assert_page_owner(page, user, action="manage permissions for")

    # Check for an existing identical permission to prevent duplicates
    filters = [
        PagePermission.page_id == data.page_id,
        PagePermission.permission == data.permission,
    ]
    if data.user_id is not None:
        filters.append(PagePermission.user_id == data.user_id)
    else:
        filters.append(PagePermission.user_id.is_(None))
    if data.workspace_id is not None:
        filters.append(PagePermission.workspace_id == data.workspace_id)
    else:
        filters.append(PagePermission.workspace_id.is_(None))

    existing = await db.execute(select(PagePermission).where(and_(*filters)))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This permission already exists",
        )

    perm = PagePermission(
        id=uuid4(),
        page_id=data.page_id,
        user_id=data.user_id,
        workspace_id=data.workspace_id,
        permission=data.permission,
    )

    db.add(perm)
    await db.commit()
    await db.refresh(perm)

    # ---- Grant Drive-level access (best-effort) ----
    if not _is_pending_drive_id(page.google_drive_id):
        drive_role = _PERM_TO_DRIVE_ROLE.get(data.permission, "reader")

        # Resolve email and type for the grantee
        email_info = await _resolve_email_for_permission(db, perm)
        if email_info is not None:
            email, perm_type = email_info
            try:
                await asyncio.to_thread(
                    _grant_drive_permission,
                    page.google_drive_id,
                    email,
                    drive_role,
                    perm_type=perm_type,
                )
                log.info(
                    "Granted Drive %s access on %s to %s (%s)",
                    drive_role, page.google_drive_id, email, perm_type,
                )
            except Exception:
                log.warning(
                    "Failed to grant Drive permission on %s to %s — "
                    "DB permission was saved, Drive sharing skipped",
                    page.google_drive_id,
                    email,
                    exc_info=True,
                )
        else:
            log.warning(
                "Could not resolve email for permission %s — "
                "Drive sharing skipped",
                perm.id,
            )

    return perm


async def revoke_permission(
    db: AsyncSession,
    user: User,
    permission_id: UUID,
) -> None:
    """Revoke a permission. Only the page owner can revoke."""
    result = await db.execute(
        select(PagePermission).where(PagePermission.id == permission_id)
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission not found or access denied",
        )

    # Load the page to verify ownership
    page = await _get_page_or_403(db, perm.page_id)
    assert_page_owner(page, user, action="manage permissions for")

    # ---- Revoke Drive-level access (best-effort) ----
    if not _is_pending_drive_id(page.google_drive_id):
        email_info = await _resolve_email_for_permission(db, perm)
        if email_info is not None:
            email, _perm_type = email_info
            try:
                await asyncio.to_thread(
                    _revoke_drive_permission,
                    page.google_drive_id,
                    email,
                )
            except Exception:
                log.warning(
                    "Failed to revoke Drive permission on %s for %s — "
                    "DB permission will still be deleted",
                    page.google_drive_id,
                    email,
                    exc_info=True,
                )
        else:
            log.warning(
                "Could not resolve email for permission %s — "
                "Drive revocation skipped",
                permission_id,
            )

    await db.delete(perm)
    await db.commit()
