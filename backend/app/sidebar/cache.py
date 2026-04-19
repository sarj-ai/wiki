"""Sidebar cache service: drive-tree persistence, stale-while-revalidate, singleflight coalescing.

Three moving parts feed the sidebar loader:

* ``drive_tree_cache`` rows in Postgres — the source of truth for warm reads.
* Per-process ``Singleflight`` that coalesces concurrent walkers so one
  real Drive call serves every waiter on the same drive.
* Background refresh tasks kicked off when a warm row is past TTL.

The module exposes a ``SidebarCacheService`` ABC so routers can depend
on the abstract shape; ``PostgresSidebarCacheService`` is the concrete
implementation wired up via ``dependencies.py``. Singleflight and the
background-task set are module-level singletons — creating a new
instance per request would defeat coalescing.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.drive.schemas import DriveNode, DriveTree
from app.drive.service import DriveService
from app.models.navigator import DriveTreeCache
from app.models.user import User
from app.sidebar.singleflight import Singleflight

# Stale-while-revalidate window — reads older than this still serve the
# cached tree but trigger an async refresh in the background.
CACHE_TTL = timedelta(minutes=10)

# Module-level singletons. Per-request instances would lose coalescing.
# Exported without a leading underscore so ``dependencies.py`` can inject
# them into the service; tests that need isolation can construct a
# service with their own Singleflight/bg_tasks instead.
SINGLEFLIGHT = Singleflight()

# Strong refs to background refresh tasks so the GC can't drop them
# mid-flight (asyncio.create_task docs, ruff RUF006).
BG_TASKS: set[asyncio.Task[None]] = set()


class SidebarCacheService(ABC):
    """Contract for drive-tree cache reads and invalidations."""

    @abstractmethod
    async def get_drive_tree(self, db: AsyncSession, drive_id: str) -> DriveTree: ...

    @abstractmethod
    async def get_folder_children(self, folder_id: str) -> list[DriveNode]: ...

    @abstractmethod
    async def invalidate_drive(self, db: AsyncSession, drive_id: str) -> None: ...

    @abstractmethod
    async def invalidate_folder(self, drive_id: str, folder_id: str) -> None: ...

    @abstractmethod
    async def list_cached_drives(self, db: AsyncSession) -> list[str]: ...


class PostgresSidebarCacheService(SidebarCacheService):
    """``drive_tree_cache``-backed implementation.

    Per-request: constructed with the acting user and their ``DriveService``
    so method signatures don't need to re-thread them. Module-level
    singletons for singleflight and background-task tracking are injected
    so tests can supply their own.
    """

    def __init__(
        self,
        *,
        drive: DriveService,
        user: User,
        singleflight: Singleflight,
        bg_tasks: set[asyncio.Task[None]],
    ) -> None:
        self._drive = drive
        self._user = user
        self._sf = singleflight
        self._bg_tasks = bg_tasks

    async def get_drive_tree(self, db: AsyncSession, drive_id: str) -> DriveTree:
        # Warm path: row exists → return it (stale is OK; refresh async).
        # Cold path: no row → run the walk through singleflight so concurrent
        # callers share one Drive call.
        row = await self._load_row(db, drive_id)
        if row is not None:
            tree = DriveTree.model_validate(row.tree)
            if datetime.now(UTC) - row.refreshed_at > CACHE_TTL:
                self._schedule_refresh(drive_id)
            return tree
        return await self._sf.do(
            f"drive:{drive_id}",
            lambda: self._walk_and_save(drive_id),
        )

    async def get_folder_children(self, folder_id: str) -> list[DriveNode]:
        return await self._sf.do(
            f"folder:{folder_id}",
            lambda: self._drive.list_folder_children(folder_id),
        )

    async def invalidate_drive(self, db: AsyncSession, drive_id: str) -> None:
        row = await self._load_row(db, drive_id)
        if row is None:
            return
        await db.delete(row)
        await db.commit()

    async def invalidate_folder(self, drive_id: str, folder_id: str) -> None:
        # Folder listings aren't persisted today; a named entry point lets
        # write paths call it now and have real eviction wired later
        # without touching every caller.
        _ = drive_id, folder_id

    async def list_cached_drives(self, db: AsyncSession) -> list[str]:
        rows = (await db.execute(select(DriveTreeCache.drive_id))).scalars().all()
        return list(rows)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _schedule_refresh(self, drive_id: str) -> None:
        task = asyncio.create_task(self._refresh_in_background(drive_id))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _refresh_in_background(self, drive_id: str) -> None:
        try:
            await self._sf.do(
                f"drive:{drive_id}",
                lambda: self._walk_and_save(drive_id),
            )
        except Exception:
            logger.exception("background drive refresh failed drive={}", drive_id)

    async def _walk_and_save(self, drive_id: str) -> DriveTree:
        tree = await self._drive.walk_drive_folders(drive_id)
        async with async_session() as db:
            await self._save_tree(db, tree)
        return tree

    async def _save_tree(self, db: AsyncSession, tree: DriveTree) -> None:
        # Last-writer-wins on refreshed_at — a slower concurrent walker
        # can't clobber a fresher result if two walks race through
        # singleflight.
        stmt = (
            pg_insert(DriveTreeCache)
            .values(
                drive_id=tree.drive_id,
                tree=tree.model_dump(mode="json"),
                refreshed_at=datetime.now(UTC),
                refreshed_by=self._user.id,
            )
            .on_conflict_do_update(
                index_elements=["drive_id"],
                set_={
                    "tree": pg_insert(DriveTreeCache).excluded.tree,
                    "refreshed_at": pg_insert(DriveTreeCache).excluded.refreshed_at,
                    "refreshed_by": pg_insert(DriveTreeCache).excluded.refreshed_by,
                },
                where=(
                    pg_insert(DriveTreeCache).excluded.refreshed_at > DriveTreeCache.refreshed_at
                ),
            )
        )
        await db.execute(stmt)
        await db.commit()

    async def _load_row(self, db: AsyncSession, drive_id: str) -> DriveTreeCache | None:
        return await db.get(DriveTreeCache, drive_id)
