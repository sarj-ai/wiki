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

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import override

from cryptography.fernet import InvalidToken
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.encryption import decrypt_text, encrypt_text
from app.config import settings
from app.drive.schemas import DriveNode, DriveTree
from app.drive.service import DriveService
from app.models.user import User
from app.models.wiki import DriveTreeCache
from app.sidebar.singleflight import Singleflight


# Retention bound for rows nobody reads anymore (refresh only happens on
# read, so an abandoned drive's folder names would otherwise sit in
# Postgres forever). Purged opportunistically on every save.
CACHE_MAX_AGE = timedelta(days=30)

# Module-level singletons. Per-request instances would lose coalescing.
# Exported without a leading underscore so ``dependencies.py`` can inject
# them into the service; tests that need isolation can construct a
# service with their own Singleflight/bg_tasks instead.
SINGLEFLIGHT = Singleflight()

# Strong refs to background refresh tasks so the GC can't drop them
# mid-flight (asyncio.create_task docs, ruff RUF006).
BG_TASKS: set[asyncio.Task[None]] = set()


@dataclass(frozen=True)
class CachedDriveTree:
    """A drive tree plus the cache stamp the router turns into an ETag.
    ``refreshed_at`` is ``None`` on a cold-cache walk (no validator yet).
    """

    tree: DriveTree
    refreshed_at: datetime | None


class SidebarCacheService(ABC):
    """Contract for drive-tree cache reads and invalidations."""

    @abstractmethod
    async def get_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree: ...

    @abstractmethod
    async def peek_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree | None:
        """Warm-row-only read: serve whatever is cached (scheduling a
        background refresh when it's past TTL) and return ``None`` on a
        cold cache instead of blocking on a multi-second Drive walk.
        For callers that merely *decorate* with the tree — the
        ``files/{id}`` breadcrumb — where a missing tree is a cosmetic
        gap but a synchronous walk would sit on the doc-open critical
        path.
        """

    @abstractmethod
    async def get_folder_children(self, folder_id: str) -> list[DriveNode]: ...

    @abstractmethod
    async def invalidate_drive(self, db: AsyncSession, drive_id: str) -> None: ...

    @abstractmethod
    async def refresh_drive(self, db: AsyncSession, drive_id: str) -> DriveTree:
        """Force a fresh walk: drop the cache row, rewalk Drive, save back,
        and return the new tree. Used by the user-triggered refresh
        button so newly-created shortcuts / folders show up without
        waiting for the TTL window. Goes through the same singleflight
        as cold reads so concurrent refresh clicks coalesce to one Drive
        call.
        """


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
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._drive = drive
        self._user = user
        self._sf = singleflight
        self._bg_tasks = bg_tasks
        self._sessionmaker = sessionmaker

    @override
    async def get_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree:
        # Warm path: row exists → return it (stale is OK; refresh async).
        # Cold path: no row → run the walk through singleflight so concurrent
        # callers share one Drive call.
        cached = await self.peek_drive_tree(db, drive_id)
        if cached is not None:
            return cached
        tree = await self._sf.do(
            f"drive:{drive_id}",
            lambda: self._walk_and_save(drive_id),
        )
        return CachedDriveTree(tree=tree, refreshed_at=None)

    @override
    async def peek_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree | None:
        row = await self._load_row(db, drive_id)
        if row is None:
            return None
        tree = await self._decode_row(db, row)
        if tree is None:
            return None
        if datetime.now(UTC) - row.refreshed_at > settings.SIDEBAR_CACHE_TTL:
            self._schedule_refresh(drive_id)
        return CachedDriveTree(tree=tree, refreshed_at=row.refreshed_at)

    @override
    async def get_folder_children(self, folder_id: str) -> list[DriveNode]:
        return await self._sf.do(
            f"folder:{folder_id}",
            lambda: self._drive.list_folder_children(folder_id),
        )

    @override
    async def invalidate_drive(self, db: AsyncSession, drive_id: str) -> None:
        row = await self._load_row(db, drive_id)
        if row is None:
            return
        await db.delete(row)
        await db.commit()

    @override
    async def refresh_drive(self, db: AsyncSession, drive_id: str) -> DriveTree:
        await self.invalidate_drive(db, drive_id)
        return await self._sf.do(
            f"drive:{drive_id}",
            lambda: self._walk_and_save(drive_id),
        )

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
        except Exception:  # noqa: BLE001 — bg fire-and-forget; log and discard so the task pool stays healthy.
            logger.exception("background drive refresh failed drive={}", drive_id)

    async def _walk_and_save(self, drive_id: str) -> DriveTree:
        tree = await self._drive.walk_drive_folders(drive_id)
        async with self._sessionmaker() as db:
            await self._save_tree(db, tree)
        return tree

    async def _decode_row(self, db: AsyncSession, row: DriveTreeCache) -> DriveTree | None:
        """Decrypt a cache row, treating an unreadable blob as a miss.

        A FERNET_KEY rotation makes every existing row undecryptable;
        deleting the row and falling through to a cold walk turns the
        rotation into a non-event.
        """
        try:
            decrypted = decrypt_text(row.tree)
        except InvalidToken:
            await db.delete(row)
            await db.commit()
            return None
        try:
            return DriveTree.model_validate_json(decrypted)
        except ValidationError:
            await db.delete(row)
            await db.commit()
            return None

    async def _save_tree(self, db: AsyncSession, tree: DriveTree) -> None:
        # Last-writer-wins on refreshed_at — a slower concurrent walker
        # can't clobber a fresher result if two walks race through
        # singleflight.
        stmt = (
            pg_insert(DriveTreeCache)
            .values(
                drive_id=tree.drive_id,
                tree=encrypt_text(tree.model_dump_json()),
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
        # Opportunistic retention purge, piggybacked on writes so no
        # scheduler is needed.
        await db.execute(
            delete(DriveTreeCache).where(
                DriveTreeCache.refreshed_at < datetime.now(UTC) - CACHE_MAX_AGE
            )
        )
        await db.commit()

    async def _load_row(self, db: AsyncSession, drive_id: str) -> DriveTreeCache | None:
        return await db.get(DriveTreeCache, drive_id)
