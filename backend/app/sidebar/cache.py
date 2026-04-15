"""Drive-tree cache read/write with singleflight and stale-while-revalidate.

See PIVOT_PLAN.md §4.2 and §7.1 for the UX contract:

* Sidebar loads are progressive and never block on the slowest drive.
* Warm cache reads return instantly; if the row is older than TTL the
  caller still gets the stale tree and a background refresh is kicked
  off so the next user sees fresh data.
* Cold cache reads walk the drive synchronously with the user's token.
* Every drive walk and every folder listing is coalesced per key via
  ``Singleflight`` so concurrent requests do one real Drive call.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.drive.schemas import DriveNode, DriveTree
from app.drive.tree_walker import list_folder_children, walk_drive_folders
from app.models.navigator import DriveTreeCache
from app.models.user import User
from app.sidebar.singleflight import Singleflight

log = logging.getLogger(__name__)

# Stale-while-revalidate window. Reads older than this still serve the
# cached tree but trigger an async refresh in the background.
CACHE_TTL = timedelta(minutes=10)

_sf = Singleflight()

# Strong refs to background refresh tasks so the GC can't drop them
# mid-flight (asyncio.create_task docs and RUF006).
_bg_tasks: set[asyncio.Task[None]] = set()


async def _load_row(db: AsyncSession, drive_id: str) -> DriveTreeCache | None:
    return await db.get(DriveTreeCache, drive_id)


async def _save_tree(
    db: AsyncSession,
    tree: DriveTree,
    refreshed_by: User,
) -> None:
    """Upsert a walked tree, last-writer-wins on ``refreshed_at``.

    The ``WHERE excluded.refreshed_at > drive_tree_cache.refreshed_at``
    clause prevents a slower concurrent walk from clobbering a fresher
    result if two walks race through singleflight (§7.3).
    """
    stmt = (
        pg_insert(DriveTreeCache)
        .values(
            drive_id=tree.drive_id,
            tree=tree.model_dump(mode="json"),
            refreshed_at=datetime.now(UTC),
            refreshed_by=refreshed_by.id,
        )
        .on_conflict_do_update(
            index_elements=["drive_id"],
            set_={
                "tree": pg_insert(DriveTreeCache).excluded.tree,
                "refreshed_at": pg_insert(DriveTreeCache).excluded.refreshed_at,
                "refreshed_by": pg_insert(DriveTreeCache).excluded.refreshed_by,
            },
            where=(
                pg_insert(DriveTreeCache).excluded.refreshed_at
                > DriveTreeCache.refreshed_at
            ),
        )
    )
    await db.execute(stmt)
    await db.commit()


def _deserialize_tree(row: DriveTreeCache) -> DriveTree:
    return DriveTree.model_validate(row.tree)


async def _walk_and_save(user: User, drive_id: str) -> DriveTree:
    """Do the real Drive walk and persist it. Called inside singleflight."""
    tree = await walk_drive_folders(user, drive_id)
    async with async_session() as db:
        await _save_tree(db, tree, user)
    return tree


async def get_drive_tree(db: AsyncSession, user: User, drive_id: str) -> DriveTree:
    """Return the cached folder tree for ``drive_id``, walking if needed.

    Fast path: warm cache → return immediately (serving stale is fine;
    a background refresh is scheduled when the row is past TTL).

    Cold path: no row yet → coalesce through singleflight so only one
    walker per drive runs at a time, then return the fresh tree.
    """
    row = await _load_row(db, drive_id)
    if row is not None:
        tree = _deserialize_tree(row)
        age = datetime.now(UTC) - row.refreshed_at
        if age > CACHE_TTL:
            # Fire-and-forget refresh. Singleflight dedupes so N users
            # hitting a stale row don't start N walks.
            task = asyncio.create_task(_refresh_drive_in_background(user, drive_id))
            _bg_tasks.add(task)
            task.add_done_callback(_bg_tasks.discard)
        return tree

    return await _sf.do(
        f"drive:{drive_id}",
        lambda: _walk_and_save(user, drive_id),
    )


async def _refresh_drive_in_background(user: User, drive_id: str) -> None:
    """Background refresh path. Errors are logged but swallowed."""
    try:
        await _sf.do(
            f"drive:{drive_id}",
            lambda: _walk_and_save(user, drive_id),
        )
    except Exception:
        log.exception("background drive refresh failed drive=%s", drive_id)


async def get_folder_children(user: User, folder_id: str) -> list[DriveNode]:
    """Lazy file-load for a single folder, coalesced per folder id.

    Unlike the drive-level cache this currently hits Drive every call
    (gated only by singleflight). Folder-level results can be added
    into ``drive_tree_cache`` by the router layer if the cost of the
    extra round-trip ever becomes a concern.
    """
    return await _sf.do(
        f"folder:{folder_id}",
        lambda: list_folder_children(user, folder_id),
    )


async def invalidate_drive(db: AsyncSession, drive_id: str) -> None:
    """Delete a drive's cache row so the next read triggers a full walk.

    Called from write paths (create/rename/move/delete) when inline
    patching the cached tree is more trouble than just rebuilding it.
    """
    row = await _load_row(db, drive_id)
    if row is None:
        return
    await db.delete(row)
    await db.commit()


async def invalidate_folder(drive_id: str, folder_id: str) -> None:
    """Marker hook for folder-level cache eviction.

    Today folder listings aren't persisted, so there's nothing to
    remove — but keeping a named entry point means write paths can
    already call it and we can add real eviction later without
    rippling changes through every caller.
    """
    _ = drive_id, folder_id
    return


async def list_cached_drives(db: AsyncSession) -> list[str]:
    """Return every drive id currently cached. Diagnostics/tests only."""
    rows = (await db.execute(select(DriveTreeCache.drive_id))).scalars().all()
    return list(rows)
