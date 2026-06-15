from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import override

from cryptography.fernet import InvalidToken
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.encryption import decrypt_cache, encrypt_cache
from app.config import settings
from app.drive.schemas import DriveNode, DriveTree
from app.drive.service import DriveService
from app.models.user import User
from app.models.wiki import DriveTreeCache
from app.sidebar.singleflight import Singleflight


CACHE_MAX_AGE = timedelta(days=30)

SINGLEFLIGHT = Singleflight()

BG_TASKS: set[asyncio.Task[None]] = set()

_DECODE_CACHE_MAX = 32

_decoded_trees: OrderedDict[tuple[str, str], DriveTree] = OrderedDict()


def _decrypt_and_parse(blob: str) -> DriveTree:
    return DriveTree.model_validate_json(decrypt_cache(blob))


def _decoded_get(key: tuple[str, str]) -> DriveTree | None:
    tree = _decoded_trees.get(key)
    if tree is not None:
        _decoded_trees.move_to_end(key)
    return tree


def _decoded_put(key: tuple[str, str], tree: DriveTree) -> None:
    _decoded_trees[key] = tree
    _decoded_trees.move_to_end(key)
    while len(_decoded_trees) > _DECODE_CACHE_MAX:
        _decoded_trees.popitem(last=False)


@dataclass(frozen=True)
class CachedDriveTree:
    tree: DriveTree
    refreshed_at: datetime | None


class SidebarCacheService(ABC):
    @abstractmethod
    async def get_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree: ...

    @abstractmethod
    async def peek_drive_tree(self, db: AsyncSession, drive_id: str) -> CachedDriveTree | None: ...

    @abstractmethod
    async def get_folder_children(self, folder_id: str) -> list[DriveNode]: ...

    @abstractmethod
    async def invalidate_drive(self, db: AsyncSession, drive_id: str) -> None: ...

    @abstractmethod
    async def refresh_drive(self, db: AsyncSession, drive_id: str) -> DriveTree: ...


class PostgresSidebarCacheService(SidebarCacheService):
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
        key = (row.drive_id, row.refreshed_at.isoformat())
        cached = _decoded_get(key)
        if cached is not None:
            return cached
        try:
            tree = await asyncio.to_thread(_decrypt_and_parse, row.tree)
        except (InvalidToken, ValidationError):
            await db.delete(row)
            await db.commit()
            return None
        _decoded_put(key, tree)
        return tree

    async def _save_tree(self, db: AsyncSession, tree: DriveTree) -> None:
        stmt = (
            pg_insert(DriveTreeCache)
            .values(
                drive_id=tree.drive_id,
                tree=encrypt_cache(tree.model_dump_json()),
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
        await db.execute(
            delete(DriveTreeCache).where(
                DriveTreeCache.refreshed_at < datetime.now(UTC) - CACHE_MAX_AGE
            )
        )
        await db.commit()

    async def _load_row(self, db: AsyncSession, drive_id: str) -> DriveTreeCache | None:
        return await db.get(DriveTreeCache, drive_id)
