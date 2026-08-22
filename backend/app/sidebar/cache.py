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
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.encryption import decrypt_cache, encrypt_cache
from app.config import settings
from app.drive.schemas import DriveNode, DriveTree
from app.drive.service import DriveService
from app.identifiers import DriveId, FileId, UserId
from app.models.user import User
from app.models.wiki import DriveTreeCache
from app.sidebar.singleflight import Singleflight


CACHE_MAX_AGE = timedelta(days=30)

SINGLEFLIGHT = Singleflight()

BG_TASKS: set[asyncio.Task[None]] = set()

_DECODED_TREE_CACHE_MAX = 32
_decoded_trees: OrderedDict[tuple[UserId, DriveId, str], DriveTree] = OrderedDict()


def _decrypt_and_parse(blob: str) -> DriveTree:
    return DriveTree.model_validate_json(decrypt_cache(blob))


def _decoded_get(key: tuple[UserId, DriveId, str]) -> DriveTree | None:
    tree = _decoded_trees.get(key)
    if tree is not None:
        _decoded_trees.move_to_end(key)
    return tree


def _decoded_put(key: tuple[UserId, DriveId, str], tree: DriveTree) -> None:
    _decoded_trees[key] = tree
    _decoded_trees.move_to_end(key)
    while len(_decoded_trees) > _DECODED_TREE_CACHE_MAX:
        _decoded_trees.popitem(last=False)


def _decoded_invalidate_drive(drive_id: DriveId) -> None:
    for key in [key for key in _decoded_trees if key[1] == drive_id]:
        del _decoded_trees[key]


@dataclass(frozen=True)
class CachedDriveTree:
    tree: DriveTree
    refreshed_at: datetime | None


class SidebarCacheService(ABC):
    @abstractmethod
    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree: ...

    @abstractmethod
    async def peek_drive_tree(
        self, db: AsyncSession, drive_id: DriveId
    ) -> CachedDriveTree | None: ...

    async def get_drive_tree_refreshed_at(
        self, db: AsyncSession, drive_id: DriveId
    ) -> datetime | None:
        cached = await self.peek_drive_tree(db, drive_id)
        return cached.refreshed_at if cached is not None else None

    @abstractmethod
    async def get_folder_children(self, folder_id: FileId) -> list[DriveNode]: ...

    @abstractmethod
    async def invalidate_drive(self, db: AsyncSession, drive_id: DriveId) -> None: ...

    @abstractmethod
    async def refresh_drive(self, db: AsyncSession, drive_id: DriveId) -> DriveTree: ...


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
    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        cached = await self.peek_drive_tree(db, drive_id)
        if cached is not None:
            return cached
        tree = await self._sf.do(
            self._singleflight_key("drive", drive_id),
            lambda: self._walk_and_save(drive_id),
        )
        return CachedDriveTree(tree=tree, refreshed_at=None)

    @override
    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree | None:
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
    async def get_drive_tree_refreshed_at(
        self, db: AsyncSession, drive_id: DriveId
    ) -> datetime | None:
        refreshed_at = await self._load_refreshed_at(db, drive_id)
        if (
            refreshed_at is not None
            and datetime.now(UTC) - refreshed_at > settings.SIDEBAR_CACHE_TTL
        ):
            self._schedule_refresh(drive_id)
        return refreshed_at

    @override
    async def get_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        return await self._sf.do(
            self._singleflight_key("folder", folder_id),
            lambda: self._drive.list_folder_children(folder_id),
        )

    @override
    async def invalidate_drive(self, db: AsyncSession, drive_id: DriveId) -> None:
        # A mutation visible to one member can stale every member's principal-
        # scoped view. Delete all rows for this drive; each principal repopulates
        # its own tree with its own Google credentials.
        await db.execute(delete(DriveTreeCache).where(DriveTreeCache.drive_id == drive_id))
        await db.commit()
        _decoded_invalidate_drive(drive_id)

    @override
    async def refresh_drive(self, db: AsyncSession, drive_id: DriveId) -> DriveTree:
        await self.invalidate_drive(db, drive_id)
        return await self._sf.do(
            self._singleflight_key("drive", drive_id),
            lambda: self._walk_and_save(drive_id),
        )

    def _schedule_refresh(self, drive_id: DriveId) -> None:
        task = asyncio.create_task(self._refresh_in_background(drive_id))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _refresh_in_background(self, drive_id: DriveId) -> None:
        try:
            await self._sf.do(
                self._singleflight_key("drive", drive_id),
                lambda: self._walk_and_save(drive_id),
            )
        except Exception as exc:  # ruff: ignore[blind-except] — detached refresh failures must not escape the task.
            logger.warning(
                "background drive refresh failed error_type={error_type}",
                error_type=type(exc).__name__,
            )

    async def _walk_and_save(self, drive_id: DriveId) -> DriveTree:
        tree = await self._drive.walk_drive_folders(drive_id)
        async with self._sessionmaker() as db:
            await self._save_tree(db, tree)
        return tree

    async def _decode_row(self, db: AsyncSession, row: DriveTreeCache) -> DriveTree | None:
        key = (UserId(self._user.id), row.drive_id, row.refreshed_at.isoformat())
        cached = _decoded_get(key)
        if cached is not None:
            return cached
        try:
            tree = await asyncio.to_thread(_decrypt_and_parse, row.tree)
        except InvalidToken, ValidationError:
            await db.delete(row)
            await db.commit()
            return None
        _decoded_put(key, tree)
        return tree

    async def _save_tree(self, db: AsyncSession, tree: DriveTree) -> None:
        stmt = (
            pg_insert(DriveTreeCache)
            .values(
                user_id=self._user.id,
                drive_id=tree.drive_id,
                tree=encrypt_cache(tree.model_dump_json()),
                refreshed_at=datetime.now(UTC),
                refreshed_by=self._user.id,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "drive_id"],
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

    async def _load_row(self, db: AsyncSession, drive_id: DriveId) -> DriveTreeCache | None:
        return await db.get(DriveTreeCache, (self._user.id, drive_id))

    async def _load_refreshed_at(self, db: AsyncSession, drive_id: DriveId) -> datetime | None:
        result = await db.execute(
            select(DriveTreeCache.refreshed_at).where(
                DriveTreeCache.user_id == self._user.id,
                DriveTreeCache.drive_id == drive_id,
            )
        )
        return result.scalar_one_or_none()

    def _singleflight_key(self, kind: str, resource_id: str) -> str:
        return f"user:{self._user.id}:{kind}:{resource_id}"
