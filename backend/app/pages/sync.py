"""Drive synchronization: incremental and full sync of pages with Google Drive."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import visible_pages_filter
from app.config import settings
from app.drive import (
    _escape_drive_query,
    batch_check_trashed,
    get_drive_changes,
    get_drive_changes_start_token,
    get_sa_drive_service,
    is_drive_folder_empty,
    rename_drive_file,
    trash_drive_file,
)
from app.models.page import Page
from app.models.user import User
from app.models.workspace import Workspace

log = logging.getLogger(__name__)

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"

# Prevent concurrent sync operations -- only one sync at a time.
_sync_lock = asyncio.Lock()

# Module-level: last known Drive Changes API token.
# Persists within a Cloud Run instance; resets on cold start (triggers full scan).
_last_changes_token: str | None = None


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def sync_drive_state(db: AsyncSession, user: User) -> dict[str, int]:
    """Sync DB with Drive: remove trashed pages, update titles, ingest new Docs.

    Tries incremental sync via the Drive Changes API first (much faster).
    Falls back to a full folder scan on first run or if incremental fails.
    Skips if another sync is already running (returns zeros).
    """
    if _sync_lock.locked():
        log.info("Sync already in progress, skipping")
        return {"removed": 0, "added": 0, "updated": 0}

    async with _sync_lock:
        global _last_changes_token

        # Try incremental sync first (much faster)
        if _last_changes_token is not None:
            result = await _incremental_sync(db, user, _last_changes_token)
            if result is not None:
                return result

        # Fall back to full scan (first run or incremental failed)
        result = await _full_sync(db, user)

        # Save token for future incremental syncs
        try:
            _last_changes_token = await asyncio.to_thread(get_drive_changes_start_token)
        except Exception:
            log.warning("Failed to get changes start token", exc_info=True)

        return result


# ------------------------------------------------------------------
# Incremental sync (Drive Changes API)
# ------------------------------------------------------------------

async def _incremental_sync(
    db: AsyncSession, user: User, start_token: str
) -> dict[str, int] | None:
    """Try an incremental sync using the Drive Changes API.

    Returns a result dict on success, or None to signal a fallback to full scan.
    """
    global _last_changes_token

    try:
        changes, new_token = await asyncio.to_thread(get_drive_changes, start_token)
    except Exception:
        log.warning("Incremental sync failed, falling back to full scan", exc_info=True)
        _last_changes_token = None  # Reset so next attempt does full scan
        return None  # Signal to fall back to full scan

    if not changes:
        _last_changes_token = new_token
        return {"removed": 0, "added": 0, "updated": 0, "pruned": 0, "cleaned": 0}

    added = 0
    removed = 0
    updated = 0

    for change in changes:
        file_id = change.get("fileId")
        if not file_id:
            continue  # Drive-level change (not file-specific), skip

        try:
            is_removed = change.get("removed", False)
            file_meta = change.get("file")

            if is_removed or (file_meta and file_meta.get("trashed")):
                # File was removed/trashed -- delete from DB if we have it
                result = await db.execute(select(Page).where(Page.google_drive_id == file_id))
                page = result.scalar_one_or_none()
                if page:
                    await db.delete(page)
                    removed += 1
                    log.info("Incremental: removed page '%s' (%s)", page.title, file_id)
                continue

            if file_meta is None:
                continue

            mime_type = file_meta.get("mimeType", "")
            file_name = file_meta.get("name", "Untitled")
            parents = file_meta.get("parents", [])

            if mime_type == GOOGLE_DOC_MIME and parents:
                parent_folder_id = parents[0]

                # Check if we already have this page
                result = await db.execute(select(Page).where(Page.google_drive_id == file_id))
                existing = result.scalar_one_or_none()

                if existing:
                    changed = False
                    # App is the source of truth for titles -- push to Drive if diverged
                    if existing.title != file_name:
                        try:
                            await asyncio.to_thread(rename_drive_file, None, file_id, existing.title)
                            log.info("Sync: renamed Drive file '%s' -> '%s'", file_name, existing.title)
                        except Exception:
                            log.warning("Sync: failed to rename Drive file %s", file_id, exc_info=True)
                    # Clear content cache so it gets re-fetched
                    existing.content_cached_at = None
                    if changed:
                        existing.updated_at = datetime.now(timezone.utc)
                        updated += 1
                        log.info("Incremental: updated '%s' (%s)", file_name, file_id)
                    else:
                        # Title unchanged but content may have changed -- still invalidate cache
                        existing.updated_at = datetime.now(timezone.utc)
                        updated += 1
                        log.info("Incremental: invalidated cache for '%s' (%s)", file_name, file_id)
                else:
                    # New doc -- need to figure out workspace/parent from folder
                    # Look up which workspace owns this folder
                    ws_result = await db.execute(
                        select(Workspace).where(Workspace.drive_folder_id == parent_folder_id)
                    )
                    workspace = ws_result.scalar_one_or_none()

                    # Check if any page uses this folder as children_folder_id
                    parent_result = await db.execute(
                        select(Page).where(
                            (Page.children_folder_id == parent_folder_id)
                            | ((Page.drive_folder_id == parent_folder_id) & (Page.type == "section"))
                        )
                    )
                    parent_page = parent_result.scalars().first()

                    workspace_id = workspace.id if workspace else (parent_page.workspace_id if parent_page else None)
                    is_private = workspace_id is None
                    parent_page_id = parent_page.id if parent_page else None

                    # Check if it's in a private folder
                    if not workspace and not parent_page:
                        # Could be in Private/{email}/ -- check parent folder
                        # For simplicity, mark as private with no parent
                        is_private = True

                    sort_result = await db.execute(
                        select(func.coalesce(func.max(Page.sort_order), 0.0)).where(
                            Page.parent_id == parent_page_id
                        )
                    )
                    sort_order = sort_result.scalar_one() + 1.0

                    new_page = Page(
                        id=uuid4(),
                        parent_id=parent_page_id,
                        google_drive_id=file_id,
                        drive_folder_id=parent_folder_id,
                        title=file_name,
                        type="page",
                        sort_order=sort_order,
                        is_private=is_private,
                        created_by=user.id,
                        workspace_id=workspace_id,
                    )
                    db.add(new_page)
                    added += 1
                    log.info("Incremental: added new doc '%s' (%s)", file_name, file_id)

            elif mime_type == GOOGLE_FOLDER_MIME:
                # Folder changes -- we don't need to do much here.
                # The important thing is if a folder was trashed (handled above).
                pass

        except Exception:
            log.warning("Incremental: failed to process change for %s, skipping", file_id, exc_info=True)
            continue

    if added or removed or updated:
        await db.commit()

    _last_changes_token = new_token
    return {"removed": removed, "added": added, "updated": updated, "pruned": 0, "cleaned": 0}


# ------------------------------------------------------------------
# Full sync (folder scan)
# ------------------------------------------------------------------

async def _full_sync(db: AsyncSession, user: User) -> dict[str, int]:
    # --- Phase 1: Remove trashed/deleted pages ---
    visibility = visible_pages_filter(user)
    result = await db.execute(select(Page).where(visibility))
    pages = list(result.scalars().all())

    removed = 0
    # Exclude pages still being created (pending-* Drive IDs don't exist in Drive yet)
    ready_pages = [p for p in pages if not p.google_drive_id.startswith("pending-")]
    if ready_pages:
        drive_ids = [p.google_drive_id for p in ready_pages]
        trashed_map = await asyncio.to_thread(batch_check_trashed, user, drive_ids)
        for page in ready_pages:
            if trashed_map.get(page.google_drive_id, False):
                log.info("Sync: removing page %s (Drive file %s trashed/deleted)", page.id, page.google_drive_id)
                await db.delete(page)
                removed += 1
        if removed:
            await db.commit()

    # --- Phase 2: Discover new Docs + update titles of existing ones ---
    added, updated = await _scan_drive_folder(db, user, settings.DRIVE_ROOT_FOLDER_ID)

    # --- Phase 3: Remove childless section pages from DB ---
    # Section pages represent Drive folders discovered during scan.
    # If a section has no page descendants, it means the folder has no
    # Google Docs inside -- remove the section from the sidebar (DB) but
    # leave the Drive folder untouched.
    result = await db.execute(
        select(Page).where(Page.type == "section")
    )
    sections = list(result.scalars().all())

    pruned = 0
    for section in sections:
        child_result = await db.execute(
            select(Page.id).where(Page.parent_id == section.id).limit(1)
        )
        if child_result.scalar_one_or_none() is not None:
            continue  # Has children, keep it
        await db.delete(section)
        pruned += 1
        log.info("Sync: pruned empty section '%s' from sidebar", section.title)

    if pruned:
        await db.commit()

    # --- Phase 4: Clean up orphan children folders in Drive ---
    # Non-section pages may have children_folder_id set (created by
    # _ensure_children_folder) but no children left in DB. If the Drive
    # folder is also truly empty, trash it to keep Drive tidy.
    result = await db.execute(
        select(Page).where(
            Page.children_folder_id.isnot(None),
            Page.type != "section",
        )
    )
    parents_with_folders = list(result.scalars().all())

    cleaned = 0
    for parent in parents_with_folders:
        child_result = await db.execute(
            select(Page.id).where(Page.parent_id == parent.id).limit(1)
        )
        if child_result.scalar_one_or_none() is not None:
            continue  # Has children, skip

        # No children in DB -- check if the Drive folder is actually empty
        try:
            empty = await asyncio.to_thread(is_drive_folder_empty, parent.children_folder_id)
        except Exception:
            continue

        if empty:
            try:
                await asyncio.to_thread(trash_drive_file, user, parent.children_folder_id)
                log.info("Sync: trashed empty children folder %s for page '%s'", parent.children_folder_id, parent.title)
            except Exception:
                log.warning("Sync: failed to trash folder %s", parent.children_folder_id, exc_info=True)
                continue
            parent.children_folder_id = None
            cleaned += 1

    if cleaned:
        await db.commit()

    return {"removed": removed, "added": added, "updated": updated, "pruned": pruned, "cleaned": cleaned}


# ------------------------------------------------------------------
# Drive folder listing
# ------------------------------------------------------------------

def _list_drive_folder(folder_id: str) -> list[dict]:
    """List all files in a Drive folder (handles pagination).

    Runs synchronously -- designed to be called from a worker thread.
    Creates its own thread-local Drive service to avoid httplib2 concurrency issues.
    Returns a list of file metadata dicts.
    """
    service = get_sa_drive_service()
    files: list[dict] = []
    page_token = None
    query = f"'{_escape_drive_query(folder_id)}' in parents and trashed = false"
    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken,files(id,name,mimeType,parents)",
            spaces="drive",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=100,
            pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


# ------------------------------------------------------------------
# Recursive folder scan
# ------------------------------------------------------------------

async def _scan_drive_folder(
    db: AsyncSession,
    user: User,
    root_folder_id: str,
) -> tuple[int, int]:
    """Recursively scan a Drive folder: ingest new Docs and update titles of existing ones.

    Returns (added, updated).
    """
    added = 0
    updated = 0

    # Build a map of existing pages by Drive ID for title sync
    result = await db.execute(select(Page))
    existing_pages = {p.google_drive_id: p for p in result.scalars().all()}

    # Scan folders recursively using a stack with parallel Drive API calls.
    # Each entry: (folder_id, parent_page_id, is_private, owner_user, workspace_id, folder_path)
    SCAN_CONCURRENCY = 5
    stack: list[tuple[str, UUID | None, bool, User | None, UUID | None, list[str]]] = [
        (root_folder_id, None, False, None, None, [])
    ]

    async def _fetch_one(fid: str) -> list[dict] | None:
        try:
            return await asyncio.to_thread(_list_drive_folder, fid)
        except Exception:
            log.warning("Sync: failed to list folder %s, skipping", fid, exc_info=True)
            return None

    while stack:
        # Pop up to SCAN_CONCURRENCY items for parallel fetching
        batch: list[tuple[str, UUID | None, bool, User | None, UUID | None, list[str]]] = []
        while stack and len(batch) < SCAN_CONCURRENCY:
            batch.append(stack.pop())

        # Fetch all folder listings in parallel (Drive API calls only)
        results = await asyncio.gather(*[_fetch_one(entry[0]) for entry in batch])

        # Process results sequentially (DB operations are not thread-safe)
        for entry, file_list in zip(batch, results):
            if file_list is None:
                continue
            folder_id, parent_page_id, is_private, owner_user, workspace_id, folder_path = entry

            for file_meta in file_list:
                file_id = file_meta["id"]
                file_name = file_meta.get("name", "Untitled")
                mime_type = file_meta.get("mimeType", "")

                if mime_type == GOOGLE_FOLDER_MIME:
                    sub_private = is_private
                    sub_owner = owner_user
                    sub_workspace_id = workspace_id
                    sub_path = folder_path + [file_name]

                    if not folder_path and file_name == "My Drafts":
                        sub_private = True
                    elif len(folder_path) == 1 and folder_path[0] == "My Drafts":
                        sub_private = True
                        r = await db.execute(select(User).where(User.email == file_name))
                        sub_owner = r.scalar_one_or_none()

                    if not is_private and not folder_path:
                        r = await db.execute(
                            select(Workspace).where(Workspace.drive_folder_id == file_id)
                        )
                        ws = r.scalar_one_or_none()
                        if ws:
                            sub_workspace_id = ws.id

                    sub_parent_page_id = parent_page_id
                    r = await db.execute(
                        select(Page).where(
                            (Page.children_folder_id == file_id)
                            | ((Page.drive_folder_id == file_id) & (Page.type == "section"))
                        )
                    )
                    existing_section = r.scalars().first()
                    if existing_section:
                        sub_parent_page_id = existing_section.id
                    else:
                        # Check if a sibling page with the same name exists --
                        # this folder is likely its children_folder created by
                        # _ensure_children_folder. Claim it instead of creating
                        # a duplicate section.
                        sibling_r = await db.execute(
                            select(Page).where(
                                Page.parent_id == parent_page_id,
                                Page.drive_folder_id == folder_id,
                                Page.title == file_name,
                                Page.children_folder_id.is_(None),
                            )
                        )
                        sibling_page = sibling_r.scalars().first()
                        if sibling_page:
                            sibling_page.children_folder_id = file_id
                            sub_parent_page_id = sibling_page.id
                            updated += 1
                            log.info("Sync: claimed folder '%s' (%s) as children of page %s", file_name, file_id, sibling_page.id)

                    if sub_parent_page_id == parent_page_id and (sub_workspace_id is not None or sub_private):
                        # Folder doesn't match any existing page -- create a
                        # section so nested docs get proper hierarchy.
                        # Skip special folders (Private, user email folders,
                        # workspace root folders).
                        is_special = (
                            (not folder_path and file_name == "My Drafts")
                            or (len(folder_path) == 1 and folder_path[0] == "My Drafts")
                            or (not folder_path and sub_workspace_id is not None)
                        )
                        if not is_special:
                            sort_result = await db.execute(
                                select(func.coalesce(func.max(Page.sort_order), 0.0)).where(
                                    Page.parent_id == parent_page_id
                                )
                            )
                            sort_order = sort_result.scalar_one() + 1.0

                            section_page = Page(
                                id=uuid4(),
                                parent_id=parent_page_id,
                                google_drive_id=f"section-{file_id}",
                                drive_folder_id=folder_id,
                                children_folder_id=file_id,
                                title=file_name,
                                type="section",
                                sort_order=sort_order,
                                is_private=sub_private,
                                created_by=sub_owner.id if sub_owner else user.id,
                                workspace_id=sub_workspace_id,
                            )
                            db.add(section_page)
                            existing_pages[f"section-{file_id}"] = section_page
                            sub_parent_page_id = section_page.id
                            added += 1
                            log.info("Sync: created section '%s' for folder %s", file_name, file_id)

                    stack.append((file_id, sub_parent_page_id, sub_private, sub_owner, sub_workspace_id, sub_path))

                elif mime_type == GOOGLE_DOC_MIME:
                    existing_page = existing_pages.get(file_id)
                    if existing_page:
                        changed = False
                        # App is the source of truth -- push title to Drive if diverged
                        if existing_page.title != file_name:
                            try:
                                await asyncio.to_thread(rename_drive_file, None, file_id, existing_page.title)
                                log.info("Sync: renamed Drive file '%s' -> '%s' (%s)", file_name, existing_page.title, file_id)
                            except Exception:
                                log.warning("Sync: failed to rename Drive file %s", file_id, exc_info=True)
                            existing_page.content_cached_at = None
                            changed = True
                        # Fix parent_id based on actual Drive folder location
                        if existing_page.parent_id != parent_page_id:
                            log.info("Sync: re-parented '%s' (%s) from %s to %s", file_name, file_id, existing_page.parent_id, parent_page_id)
                            existing_page.parent_id = parent_page_id
                            changed = True
                        # Fix drive_folder_id
                        if existing_page.drive_folder_id != folder_id:
                            existing_page.drive_folder_id = folder_id
                            changed = True
                        # Fix workspace_id
                        if existing_page.workspace_id != workspace_id:
                            existing_page.workspace_id = workspace_id
                            existing_page.is_private = is_private
                            changed = True
                        if changed:
                            existing_page.updated_at = datetime.now(timezone.utc)
                            updated += 1
                    else:
                        # New Google Doc -- ingest it
                        sort_result = await db.execute(
                            select(func.coalesce(func.max(Page.sort_order), 0.0)).where(
                                Page.parent_id == parent_page_id
                            )
                        )
                        sort_order = sort_result.scalar_one() + 1.0

                        new_page = Page(
                            id=uuid4(),
                            parent_id=parent_page_id,
                            google_drive_id=file_id,
                            drive_folder_id=folder_id,
                            title=file_name,
                            type="page",
                            sort_order=sort_order,
                            is_private=is_private,
                            created_by=owner_user.id if owner_user else user.id,
                            workspace_id=workspace_id,
                        )
                        db.add(new_page)
                        existing_pages[file_id] = new_page
                        added += 1
                        log.info("Sync: ingested new doc '%s' (%s)", file_name, file_id)

    if added or updated:
        await db.commit()

    return added, updated
