from __future__ import annotations

import asyncio
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import assert_can_manage_permissions
from app.drive import get_sa_drive_service
from app.models.page import Page, PagePermission
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMembership
from app.permissions.schemas import PermissionCreate

log = logging.getLogger(__name__)

# Map our permission levels to Google Drive roles
_PERM_TO_DRIVE_ROLE: dict[str, str] = {
    "view": "reader",
    "edit": "writer",
}

# Limit concurrent Drive API calls to avoid quota issues
_DRIVE_SEMAPHORE = asyncio.Semaphore(5)


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


def _is_skippable_drive_id(google_drive_id: str) -> bool:
    """Check whether the drive ID should be skipped for Drive API calls."""
    return _is_pending_drive_id(google_drive_id) or google_drive_id.startswith("section-")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _get_page_or_403(db: AsyncSession, page_id: UUID) -> Page:
    """Fetch a page by id or raise 403 (never 404 -- don't leak existence)."""
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
# Descendant & inheritance helpers
# ------------------------------------------------------------------

async def _get_descendant_page_ids(db: AsyncSession, page_id: UUID) -> list[UUID]:
    """Get all descendant page IDs (excluding the root) via recursive CTE."""
    base = select(Page.id).where(Page.parent_id == page_id)
    cte = base.cte(name="descendants", recursive=True)
    recursive = select(Page.id).join(cte, Page.parent_id == cte.c.id)
    cte = cte.union_all(recursive)

    result = await db.execute(select(cte.c.id))
    return [row[0] for row in result.all()]


async def _drive_grant_with_semaphore(
    file_id: str,
    email: str,
    role: str,
    *,
    perm_type: str = "user",
) -> None:
    """Grant a Drive permission with concurrency limiting."""
    async with _DRIVE_SEMAPHORE:
        try:
            await asyncio.to_thread(
                _grant_drive_permission, file_id, email, role, perm_type=perm_type,
            )
        except Exception:
            log.warning("Failed to grant Drive perm on %s", file_id, exc_info=True)


async def _drive_revoke_with_semaphore(file_id: str, email: str) -> None:
    """Revoke a Drive permission with concurrency limiting."""
    async with _DRIVE_SEMAPHORE:
        try:
            await asyncio.to_thread(_revoke_drive_permission, file_id, email)
        except Exception:
            log.warning("Failed to revoke Drive perm on %s", file_id, exc_info=True)


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def list_permissions(
    db: AsyncSession,
    user: User,
    page_id: UUID,
) -> list[dict]:
    """List all permissions for a page. Owner or workspace member can list."""
    page = await _get_page_or_403(db, page_id)
    await assert_can_manage_permissions(db, page, user)

    result = await db.execute(
        select(PagePermission).where(PagePermission.page_id == page_id)
    )
    perms = list(result.scalars().all())

    # Batch-fetch all referenced users and workspaces to avoid N+1
    user_ids = {p.user_id for p in perms if p.user_id is not None}
    workspace_ids = {p.workspace_id for p in perms if p.workspace_id is not None}

    users_by_id: dict[UUID, User] = {}
    if user_ids:
        u_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_by_id = {u.id: u for u in u_result.scalars().all()}

    workspaces_by_id: dict[UUID, Workspace] = {}
    if workspace_ids:
        ws_result = await db.execute(select(Workspace).where(Workspace.id.in_(workspace_ids)))
        workspaces_by_id = {ws.id: ws for ws in ws_result.scalars().all()}

    enriched = []
    for perm in perms:
        row: dict = {
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
            u_obj = users_by_id.get(perm.user_id)
            if u_obj:
                row["display_name"] = u_obj.display_name
                row["email"] = u_obj.email
                row["avatar_url"] = u_obj.avatar_url
        elif perm.workspace_id is not None:
            ws_obj = workspaces_by_id.get(perm.workspace_id)
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
    # Escape ILIKE special chars to prevent wildcard injection (% matches all, _ matches any char)
    escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
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
            # Insert a stub user record (no tokens -- they haven't logged in)
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


async def search_workspaces(
    db: AsyncSession,
    query: str,
    *,
    user_id: UUID,
    limit: int = 5,
) -> list[Workspace]:
    """Search workspaces by name or group email that the user is a member of."""
    escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"

    # Only return workspaces the user belongs to
    user_workspace_ids = (
        select(WorkspaceMembership.workspace_id)
        .where(WorkspaceMembership.user_id == user_id)
    )

    stmt = (
        select(Workspace)
        .where(
            Workspace.id.in_(user_workspace_ids),
            (Workspace.name.ilike(pattern))
            | (Workspace.google_group_email.ilike(pattern)),
        )
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


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
    """Grant permission on a page. Owner or workspace member can grant.

    When ``data.recursive`` is True, the same permission is also granted
    to every descendant page.
    """
    page = await _get_page_or_403(db, data.page_id)
    await assert_can_manage_permissions(db, page, user)

    # Check for an existing identical permission to prevent duplicates.
    # Uses FOR UPDATE to serialize concurrent grants on the same page.
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

    existing = await db.execute(
        select(PagePermission).where(and_(*filters)).with_for_update()
    )
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
        inheritable=data.recursive,
    )
    db.add(perm)
    await db.commit()
    await db.refresh(perm)

    # ---- Best-effort Drive sharing ----
    # Resolve email once; reuse for root + descendants.
    email_info = await _resolve_email_for_permission(db, perm)
    if email_info is not None:
        email, perm_type = email_info
        drive_role = _PERM_TO_DRIVE_ROLE.get(data.permission, "reader")

        # Grant on the root page itself
        if not _is_skippable_drive_id(page.google_drive_id):
            try:
                await asyncio.to_thread(
                    _grant_drive_permission, page.google_drive_id, email, drive_role, perm_type=perm_type,
                )
            except Exception:
                log.warning("Failed to grant Drive perm on %s", page.google_drive_id, exc_info=True)

        # Recursive: also grant Drive access on existing descendants (parallel)
        if data.recursive:
            descendant_ids = await _get_descendant_page_ids(db, data.page_id)
            if descendant_ids:
                desc_result = await db.execute(
                    select(Page).where(Page.id.in_(descendant_ids))
                )
                tasks = []
                for desc_page in desc_result.scalars():
                    if _is_skippable_drive_id(desc_page.google_drive_id):
                        continue
                    tasks.append(
                        _drive_grant_with_semaphore(
                            desc_page.google_drive_id, email, drive_role, perm_type=perm_type,
                        )
                    )
                if tasks:
                    await asyncio.gather(*tasks)

    return perm


async def revoke_permission(
    db: AsyncSession,
    user: User,
    permission_id: UUID,
    *,
    recursive: bool = False,
) -> None:
    """Revoke a permission.

    Deletes the single DB record. If the permission was inheritable,
    descendants automatically lose access (ancestor-walk check handles it).
    ``recursive`` controls whether Drive-level access is also revoked
    from existing descendant files.
    """
    result = await db.execute(
        select(PagePermission).where(PagePermission.id == permission_id)
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission not found or access denied",
        )

    # Load the page to verify permission management access
    page = await _get_page_or_403(db, perm.page_id)
    await assert_can_manage_permissions(db, page, user)

    was_inheritable = perm.inheritable
    email_info = await _resolve_email_for_permission(db, perm)

    # Delete the DB record -- ancestor-walk handles descendant visibility
    await db.delete(perm)
    await db.commit()

    # ---- Best-effort Drive revocation ----
    if email_info is None:
        return

    email, _pt = email_info

    # Revoke on this page
    if not _is_skippable_drive_id(page.google_drive_id):
        try:
            await asyncio.to_thread(_revoke_drive_permission, page.google_drive_id, email)
        except Exception:
            log.warning("Failed to revoke Drive perm on %s", page.google_drive_id, exc_info=True)

    # If it was inheritable, also revoke Drive access on descendants (parallel)
    if was_inheritable:
        descendant_ids = await _get_descendant_page_ids(db, page.id)
        if descendant_ids:
            desc_result = await db.execute(
                select(Page).where(Page.id.in_(descendant_ids))
            )
            tasks = []
            for desc_page in desc_result.scalars():
                if _is_skippable_drive_id(desc_page.google_drive_id):
                    continue
                tasks.append(
                    _drive_revoke_with_semaphore(desc_page.google_drive_id, email)
                )
            if tasks:
                await asyncio.gather(*tasks)


async def update_permission(
    db: AsyncSession,
    user: User,
    permission_id: UUID,
    new_permission: str,
) -> PagePermission:
    """Change a permission's level (view / edit)."""
    result = await db.execute(
        select(PagePermission).where(PagePermission.id == permission_id)
    )
    perm = result.scalar_one_or_none()
    if perm is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission not found or access denied",
        )

    page = await _get_page_or_403(db, perm.page_id)
    await assert_can_manage_permissions(db, page, user)

    old_permission = perm.permission
    perm.permission = new_permission
    await db.commit()
    await db.refresh(perm)

    # Update Drive-level access (best-effort): revoke old, grant new
    if old_permission != new_permission and not _is_skippable_drive_id(page.google_drive_id):
        email_info = await _resolve_email_for_permission(db, perm)
        if email_info:
            email, perm_type = email_info
            new_role = _PERM_TO_DRIVE_ROLE.get(new_permission, "reader")
            try:
                # Drive replaces existing permission for the same email
                await asyncio.to_thread(
                    _grant_drive_permission, page.google_drive_id, email, new_role, perm_type=perm_type,
                )
            except Exception:
                log.warning("Failed to update Drive perm on %s", page.google_drive_id, exc_info=True)

    return perm
