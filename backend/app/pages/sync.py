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
    is_drive_file_trashed,
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
# Persists within a single process; resets on cold start (triggers full scan).
# Not shared across multiple Cloud Run instances — each instance tracks independently.
# This is acceptable: webhooks handle real-time detection; incremental sync is
# a performance optimization, and full scan is the reliable fallback.
_last_changes_token: str | None = None

# Timestamp of the last successful full scan. Used to skip expensive
# folder scans on subsequent syncs when the data is still fresh.
_last_full_scan_at: float = 0.0
_FULL_SCAN_COOLDOWN = 3600  # 1 hour — skip full scan if done recently


# ------------------------------------------------------------------
# Public service functions
# ------------------------------------------------------------------

async def sync_drive_state(db: AsyncSession, user: User, *, force: bool = False) -> dict[str, int]:
    """Sync DB with Drive: remove trashed pages, update titles, ingest new Docs.

    Tries incremental sync via the Drive Changes API first (much faster).
    Falls back to a full folder scan on first run or if incremental fails.
    When force=True, always does a full scan regardless of the token.
    Skips if another sync is already running (returns zeros).
    """
    if _sync_lock.locked():
        log.info("Sync already in progress, skipping")
        return {"removed": 0, "added": 0, "updated": 0}

    async with _sync_lock:
        global _last_changes_token, _last_full_scan_at
        from app.database import async_session

        # Try incremental sync first (fast — only processes changes since last token)
        if _last_changes_token is not None and not force:
            log.info("Sync: attempting incremental sync (token=%s...)", _last_changes_token[:12])
            result = await _incremental_sync(db, user, _last_changes_token)
            if result is not None:
                log.info("Sync: incremental complete: %s", result)
                return result

        # Full scan: collect from Drive first (slow, no DB needed), then apply.
        # Uses a FRESH DB session because the request session may timeout during
        # the long Drive collection phase.
        log.info("Sync: starting FULL scan (force=%s, token=%s)", force, _last_changes_token is not None)
        try:
            drive_data = await _collect_drive_tree(settings.DRIVE_ROOT_FOLDER_ID)
        except Exception:
            log.exception("Sync: failed to collect Drive tree")
            raise

        async with async_session() as fresh_db:
            try:
                result = await _full_sync(fresh_db, user, drive_data)
                log.info("Sync: full scan complete: %s", result)
            except Exception:
                log.exception("Sync: full scan failed during DB apply phase")
                await fresh_db.rollback()
                raise

        # Save token so all subsequent syncs use fast incremental path
        try:
            _last_changes_token = await asyncio.to_thread(get_drive_changes_start_token)
            log.info("Sync: saved changes token — future syncs will be incremental")
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

        # Use a savepoint so one failed change doesn't discard prior successes
        savepoint = await db.begin_nested()
        try:
            is_removed = change.get("removed", False)
            file_meta = change.get("file")

            if is_removed or (file_meta and file_meta.get("trashed")):
                # Verify actual file state before deleting — Changes API can
                # report misleading metadata.
                result = await db.execute(select(Page).where(Page.google_drive_id == file_id))
                page = result.scalar_one_or_none()
                if page:
                    trashed = await asyncio.to_thread(is_drive_file_trashed, None, file_id)
                    if trashed is None or trashed is True:
                        await db.delete(page)
                        removed += 1
                        log.info("Incremental: removed page '%s' (%s, confirmed)", page.title, file_id)
                    else:
                        log.info(
                            "Incremental: file %s reported removed/trashed but still exists — keeping '%s'",
                            file_id, page.title,
                        )
                await savepoint.commit()
                continue

            if file_meta is None:
                await savepoint.commit()
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
                    # App is the source of truth for titles -- push to Drive if diverged
                    if existing.title != file_name:
                        try:
                            await asyncio.to_thread(rename_drive_file, None, file_id, existing.title)
                            log.info("Sync: renamed Drive file '%s' -> '%s'", file_name, existing.title)
                        except Exception:
                            log.warning("Sync: failed to rename Drive file %s", file_id, exc_info=True)
                    # Clear content cache so it gets re-fetched
                    existing.content_cached_at = None
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

                    # created_by is None for externally-created docs discovered
                    # by sync — the sync-triggering user should NOT become owner.
                    new_page = Page(
                        id=uuid4(),
                        parent_id=parent_page_id,
                        google_drive_id=file_id,
                        drive_folder_id=parent_folder_id,
                        title=file_name,
                        type="page",
                        sort_order=sort_order,
                        is_private=is_private,
                        created_by=None,
                        workspace_id=workspace_id,
                    )
                    db.add(new_page)
                    added += 1
                    log.info("Incremental: added new doc '%s' (%s)", file_name, file_id)

            elif mime_type == GOOGLE_FOLDER_MIME:
                # Folder changes -- we don't need to do much here.
                # The important thing is if a folder was trashed (handled above).
                pass

            await savepoint.commit()  # Commit this savepoint on success

        except Exception:
            log.warning("Incremental: failed to process change for %s, skipping", file_id, exc_info=True)
            await savepoint.rollback()  # Roll back only this change, keep prior successes
            continue

    if added or removed or updated:
        await db.commit()

    _last_changes_token = new_token
    return {"removed": removed, "added": added, "updated": updated, "pruned": 0, "cleaned": 0}


# ------------------------------------------------------------------
# Quick sync (trash check only, no folder scan)
# ------------------------------------------------------------------

async def _quick_sync(db: AsyncSession, user: User) -> dict[str, int]:
    """Fast sync: only check for trashed/deleted pages. No folder scan.

    Used when a full scan was done recently and we just want to clean up
    any pages whose Drive files were trashed since the last sync.
    """
    visibility = visible_pages_filter(user)
    result = await db.execute(select(Page).where(visibility))
    pages = list(result.scalars().all())

    removed = 0
    ready_pages = [p for p in pages if not p.google_drive_id.startswith("pending-") and not p.google_drive_id.startswith("section-")]
    if ready_pages:
        drive_ids = [p.google_drive_id for p in ready_pages]
        trashed_map = await asyncio.to_thread(batch_check_trashed, user, drive_ids)
        for page in ready_pages:
            if trashed_map.get(page.google_drive_id, False):
                log.info("Quick sync: removing trashed page '%s'", page.title)
                await db.delete(page)
                removed += 1
        if removed:
            await db.commit()

    return {"removed": removed, "added": 0, "updated": 0}


# ------------------------------------------------------------------
# Full sync (folder scan)
# ------------------------------------------------------------------

async def _full_sync(db: AsyncSession, user: User, drive_data: dict) -> dict[str, int]:
    # --- Phase 1: Remove trashed/deleted pages ---
    visibility = visible_pages_filter(user)
    result = await db.execute(select(Page).where(visibility))
    pages = list(result.scalars().all())

    removed = 0
    # Check regular pages (not pending/section) against Drive
    ready_pages = [p for p in pages if not p.google_drive_id.startswith("pending-") and not p.google_drive_id.startswith("section-")]
    if ready_pages:
        drive_ids = [p.google_drive_id for p in ready_pages]
        trashed_map = await asyncio.to_thread(batch_check_trashed, user, drive_ids)
        for page in ready_pages:
            if trashed_map.get(page.google_drive_id, False):
                log.info("Sync: removing page %s (Drive file %s trashed/deleted)", page.id, page.google_drive_id)
                await db.delete(page)
                removed += 1

    # Check section pages — verify their underlying Drive folder still exists
    section_pages = [p for p in pages if p.google_drive_id.startswith("section-") and p.children_folder_id]
    if section_pages:
        folder_ids = [p.children_folder_id for p in section_pages]
        folder_trashed_map = await asyncio.to_thread(batch_check_trashed, user, folder_ids)
        for section in section_pages:
            if folder_trashed_map.get(section.children_folder_id, False):
                log.info("Sync: removing section '%s' (Drive folder %s trashed/deleted)", section.title, section.children_folder_id)
                # Orphan children to workspace root before deleting section
                await db.execute(
                    Page.__table__.update()
                    .where(Page.parent_id == section.id)
                    .values(parent_id=section.parent_id)
                )
                await db.delete(section)
                removed += 1

    if removed:
        await db.commit()

    # --- Phase 2: Discover new Docs + update titles of existing ones ---
    added, updated = await _apply_drive_tree(db, user, drive_data)

    # --- Phase 3: Remove empty section pages from DB ---
    # Prune bottom-up: repeatedly remove leaf sections (no children) until
    # no more can be removed. This handles nested empty sections like
    # Security > EVIDENCE-REGISTRY (both empty).
    visibility = visible_pages_filter(user)

    pruned = 0
    while True:
        result = await db.execute(
            select(Page).where(Page.type == "section", visibility)
        )
        sections = list(result.scalars().all())

        pruned_this_pass = 0
        for section in sections:
            child_result = await db.execute(
                select(Page.id).where(Page.parent_id == section.id).limit(1)
            )
            if child_result.scalar_one_or_none() is not None:
                # Has children — but check if the underlying Drive folder still exists
                if section.children_folder_id:
                    try:
                        from app.drive import is_drive_file_trashed
                        trashed = await asyncio.to_thread(is_drive_file_trashed, None, section.children_folder_id)
                        if trashed is True or trashed is None:
                            await db.execute(
                                Page.__table__.update()
                                .where(Page.parent_id == section.id)
                                .values(parent_id=section.parent_id)
                            )
                            await db.delete(section)
                            pruned += 1
                            pruned_this_pass += 1
                            log.info("Sync: removed section '%s' (Drive folder trashed)", section.title)
                            continue
                    except Exception:
                        pass
                continue  # Has children, folder exists — keep it
            await db.delete(section)
            pruned += 1
            pruned_this_pass += 1
            log.info("Sync: pruned empty section '%s' from sidebar", section.title)

        if pruned_this_pass:
            await db.commit()
        else:
            break  # No more empty sections to prune

    if pruned:
        log.info("Sync: pruned %d empty sections total", pruned)

    # --- Phase 4: Clean up orphan children folders in Drive ---
    # Non-section pages may have children_folder_id set (created by
    # _ensure_children_folder) but no children left in DB. If the Drive
    # folder is also truly empty, trash it to keep Drive tidy.
    # Scoped to visible pages to avoid touching other users' folders.
    result = await db.execute(
        select(Page).where(
            Page.children_folder_id.isnot(None),
            Page.type != "section",
            visibility,
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

def _list_drive_folder_with_retry(folder_id: str, max_retries: int = 2) -> list[dict]:
    """List files in a Drive folder with retry on transient errors."""
    import time as _time

    service = get_sa_drive_service()
    files: list[dict] = []
    page_token = None
    query = f"'{_escape_drive_query(folder_id)}' in parents and trashed = false"

    while True:
        for attempt in range(max_retries + 1):
            try:
                resp = service.files().list(
                    q=query,
                    fields="nextPageToken,files(id,name,mimeType,parents)",
                    spaces="drive",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000,
                    pageToken=page_token,
                ).execute()
                break
            except Exception:
                if attempt < max_retries:
                    _time.sleep(1 * (attempt + 1))
                    continue
                raise

        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


async def _collect_drive_tree(root_folder_id: str) -> dict:
    """Collect all folders and docs from Drive recursively with concurrency.

    Pure Drive API work — no DB access. Uses concurrent folder listing
    to handle large folder trees efficiently.

    Returns {"folders": {id: {name, parent}}, "docs": {id: {name, parent}}}.
    """
    from collections import deque

    all_folders: dict[str, dict] = {}
    all_docs: dict[str, dict] = {}
    queue: deque[str] = deque([root_folder_id])
    visited: set[str] = set()
    CONCURRENCY = 5

    async def _fetch_one(folder_id: str) -> tuple[str, list[dict]]:
        try:
            result = await asyncio.to_thread(_list_drive_folder_with_retry, folder_id)
            return folder_id, result
        except Exception:
            log.warning("Collect: failed to list folder %s, skipping", folder_id, exc_info=True)
            return folder_id, []

    scanned = 0
    while queue:
        batch = []
        while queue and len(batch) < CONCURRENCY:
            fid = queue.popleft()
            if fid in visited:
                continue
            visited.add(fid)
            batch.append(fid)

        if not batch:
            break

        results = await asyncio.gather(*[_fetch_one(fid) for fid in batch])

        for folder_id, file_list in results:
            for f in file_list:
                fid = f["id"]
                fname = f.get("name", "Untitled")
                mime = f.get("mimeType", "")
                if mime == GOOGLE_FOLDER_MIME:
                    all_folders[fid] = {"name": fname, "parent": folder_id}
                    queue.append(fid)
                elif mime == GOOGLE_DOC_MIME:
                    all_docs[fid] = {"name": fname, "parent": folder_id}

        scanned += len(batch)
        if scanned % 50 == 0:
            log.info("Collect: scanned %d folders so far (%d folders, %d docs found)...",
                     scanned, len(all_folders), len(all_docs))

    log.info("Collect: done — %d folders, %d docs found under root", len(all_folders), len(all_docs))
    return {"folders": all_folders, "docs": all_docs, "root": root_folder_id}


# ------------------------------------------------------------------
# Apply collected Drive data to DB
# ------------------------------------------------------------------

async def _apply_drive_tree(
    db: AsyncSession,
    user: User,
    drive_data: dict,
) -> tuple[int, int]:
    """Apply pre-collected Drive data to the DB: create sections and ingest docs.

    Returns (added, updated).
    """
    all_folders = drive_data["folders"]
    all_docs = drive_data["docs"]
    root_folder_id = drive_data["root"]

    # Pre-compute which folders need sections (have docs in subtree)
    folders_with_docs: set[str] = set()
    for doc_meta in all_docs.values():
        current = doc_meta["parent"]
        while current and current != root_folder_id:
            if current in folders_with_docs:
                break
            folders_with_docs.add(current)
            current = all_folders.get(current, {}).get("parent")

    log.info("Apply: %d folders, %d docs, %d folders with docs",
             len(all_folders), len(all_docs), len(folders_with_docs))

    # Load existing DB state
    result = await db.execute(select(Page))
    existing_pages = {p.google_drive_id: p for p in result.scalars().all()}

    ws_result = await db.execute(select(Workspace))
    ws_by_folder = {ws.drive_folder_id: ws for ws in ws_result.scalars().all()}

    user_result = await db.execute(select(User))
    users_by_email = {u.email: u for u in user_result.scalars().all()}

    added = 0
    updated = 0
    db.autoflush = False

    # BFS from root — create sections (parents first) then docs
    FolderInfo = tuple[UUID | None, UUID | None, bool, User | None]  # page_id, ws_id, is_private, owner
    folder_map: dict[str, FolderInfo] = {root_folder_id: (None, None, False, None)}
    sort_counters: dict[UUID | None, float] = {}

    def _next_sort(parent_page_id: UUID | None) -> float:
        val = sort_counters.get(parent_page_id, 0.0)
        sort_counters[parent_page_id] = val + 1.0
        return val + 1.0

    # Sort folders by depth (BFS order) to ensure parents are created first
    def _folder_depth(fid: str) -> int:
        d = 0
        cur = fid
        while cur in all_folders:
            cur = all_folders[cur]["parent"]
            d += 1
        return d

    sorted_folder_ids = sorted(all_folders.keys(), key=_folder_depth)

    for fid in sorted_folder_ids:
        fmeta = all_folders[fid]
        parent_folder = fmeta["parent"]
        fname = fmeta["name"]

        if parent_folder not in folder_map:
            continue  # Parent not reachable

        parent_page_id, ws_id, is_private, owner_user = folder_map[parent_folder]

        # Determine workspace/privacy
        sub_ws_id = ws_id
        sub_private = is_private
        sub_owner = owner_user

        if parent_folder == root_folder_id:
            if fname == "My Drafts":
                sub_private = True
            elif fid in ws_by_folder:
                sub_ws_id = ws_by_folder[fid].id

        if is_private:
            parent_name = all_folders.get(parent_folder, {}).get("name", "")
            if parent_name == "My Drafts" and fname in users_by_email:
                sub_owner = users_by_email[fname]

        # Create section only for folders with docs in subtree
        if fid in folders_with_docs:
            is_ws_root = parent_folder == root_folder_id and fid in ws_by_folder
            is_my_drafts = parent_folder == root_folder_id and fname == "My Drafts"
            is_user_folder = is_private and (all_folders.get(parent_folder, {}).get("name") == "My Drafts")

            if not is_ws_root and not is_my_drafts and not is_user_folder:
                section_key = f"section-{fid}"
                existing_section = existing_pages.get(section_key)
                if existing_section:
                    # Fix workspace_id if it wasn't propagated in a previous sync
                    if existing_section.workspace_id != sub_ws_id:
                        existing_section.workspace_id = sub_ws_id
                        existing_section.is_private = sub_private
                    folder_map[fid] = (existing_section.id, sub_ws_id, sub_private, sub_owner)
                    continue

                # Check if a page already uses this folder as its children_folder_id.
                # This happens when pages are created as children in the app — the app
                # creates a Drive folder and sets children_folder_id on the parent page.
                # The sync should NOT create a duplicate section for that folder.
                parent_with_folder = await db.execute(
                    select(Page).where(Page.children_folder_id == fid)
                )
                owner_page = parent_with_folder.scalar_one_or_none()
                if owner_page:
                    folder_map[fid] = (owner_page.id, sub_ws_id, sub_private, sub_owner)
                    continue

                else:
                    section_page = Page(
                        id=uuid4(),
                        parent_id=parent_page_id,
                        google_drive_id=section_key,
                        drive_folder_id=parent_folder,
                        children_folder_id=fid,
                        title=fname,
                        type="section",
                        sort_order=_next_sort(parent_page_id),
                        is_private=sub_private,
                        created_by=sub_owner.id if sub_owner else None,
                        workspace_id=sub_ws_id,
                    )
                    db.add(section_page)
                    await db.flush()
                    existing_pages[section_key] = section_page
                    folder_map[fid] = (section_page.id, sub_ws_id, sub_private, sub_owner)
                    added += 1
                    log.info("Sync: created section '%s'", fname)
                    continue

        # Folder without docs — pass through
        folder_map[fid] = (parent_page_id, sub_ws_id, sub_private, sub_owner)

    # Now create/update docs
    for doc_id, doc_meta in all_docs.items():
        parent_folder = doc_meta["parent"]
        doc_name = doc_meta["name"]

        if parent_folder not in folder_map:
            continue

        parent_page_id, ws_id, is_private, owner_user = folder_map[parent_folder]

        existing_page = existing_pages.get(doc_id)
        if existing_page:
            changed = False
            if existing_page.title != doc_name:
                try:
                    await asyncio.to_thread(rename_drive_file, None, doc_id, existing_page.title)
                except Exception:
                    log.warning("Sync: failed to rename %s", doc_id, exc_info=True)
                existing_page.content_cached_at = None
                changed = True
            if existing_page.parent_id != parent_page_id:
                existing_page.parent_id = parent_page_id
                changed = True
            if existing_page.drive_folder_id != parent_folder:
                existing_page.drive_folder_id = parent_folder
                changed = True
            if existing_page.workspace_id != ws_id:
                existing_page.workspace_id = ws_id
                existing_page.is_private = is_private
                changed = True
            if changed:
                existing_page.updated_at = datetime.now(timezone.utc)
                updated += 1
        else:
            new_page = Page(
                id=uuid4(),
                parent_id=parent_page_id,
                google_drive_id=doc_id,
                drive_folder_id=parent_folder,
                title=doc_name,
                type="page",
                sort_order=_next_sort(parent_page_id),
                is_private=is_private,
                created_by=owner_user.id if owner_user else None,
                workspace_id=ws_id,
            )
            db.add(new_page)
            await db.flush()
            existing_pages[doc_id] = new_page
            added += 1
            log.info("Sync: ingested '%s' (%s)", doc_name, doc_id)

    db.autoflush = True
    log.info("Scan complete: added=%d, updated=%d", added, updated)

    if added or updated:
        try:
            await db.commit()
            log.info("Scan: committed %d added, %d updated", added, updated)
        except Exception:
            log.exception("Scan: commit FAILED")
            await db.rollback()

    return added, updated
