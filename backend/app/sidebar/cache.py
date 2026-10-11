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
from app.auth.google_tokens import provider_refresh_scope
from app.config import settings
from app.drive.schemas import DriveNode, DriveTree
from app.drive.service import DriveService
from app.identifiers import DriveId, FileId, UserId
from app.models.user import User
from app.models.wiki import DriveTreeCache
from app.sidebar.singleflight import Singleflight


CACHE_MAX_AGE = timedelta(days=30)

type SidebarFlightResult = DriveTree | list[DriveNode]

SINGLEFLIGHT = Singleflight[SidebarFlightResult]()
_DECODE_FLIGHTS = Singleflight[DriveTree]()

BG_TASKS: set[asyncio.Task[None]] = set()
_background_refreshes: dict[str, asyncio.Task[None]] = {}

_DECODED_TREE_CACHE_MAX = 32
_DECODED_TREE_CACHE_NODE_MAX = 40_000


class _DecodedTreeCache:
    def __init__(self, *, max_entries: int, max_nodes: int) -> None:
        self._max_entries = max_entries
        self._max_nodes = max_nodes
        self._values: OrderedDict[tuple[UserId, DriveId, str], DriveTree] = OrderedDict()

    def get(self, key: tuple[UserId, DriveId, str]) -> DriveTree | None:
        tree = self._values.get(key)
        if tree is not None:
            self._values.move_to_end(key)
        return tree

    def put(self, key: tuple[UserId, DriveId, str], tree: DriveTree) -> None:
        if len(tree.folders) + len(tree.files) > self._max_nodes:
            return
        self._values[key] = tree
        self._values.move_to_end(key)
        while (
            len(self._values) > self._max_entries
            or sum(len(value.folders) + len(value.files) for value in self._values.values())
            > self._max_nodes
        ):
            self._values.popitem(last=False)

    def invalidate_drive(self, drive_id: DriveId) -> None:
        for key in [key for key in self._values if key[1] == drive_id]:
            self._values.pop(key)


_decoded_trees = _DecodedTreeCache(
    max_entries=_DECODED_TREE_CACHE_MAX, max_nodes=_DECODED_TREE_CACHE_NODE_MAX
)


def _decrypt_and_parse(blob: str) -> DriveTree:
    return DriveTree.model_validate_json(decrypt_cache(blob))


def _encrypt_tree(tree: DriveTree) -> str:
    return encrypt_cache(tree.model_dump_json())


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
        singleflight: Singleflight[SidebarFlightResult],
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
        if (cached := await self.peek_drive_tree(db, drive_id)) is not None:
            return cached
        tree = await self._sf.do(
            self._singleflight_key("drive", drive_id),
            lambda: self._walk_and_save(drive_id),
        )
        if not isinstance(tree, DriveTree):
            msg = "Drive singleflight returned folder children"
            raise TypeError(msg)
        return CachedDriveTree(tree=tree, refreshed_at=None)

    @override
    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree | None:
        row = await self._load_row(db, drive_id)
        # The request session uses expire_on_commit=False. Release its read
        # transaction before decoding or provider work needs another session.
        await db.commit()
        if row is None:
            return None
        if (tree := await self._decode_row(db, row)) is None:
            return None
        if datetime.now(UTC) - row.refreshed_at > settings.SIDEBAR_CACHE_TTL:
            self._schedule_refresh(drive_id)
        return CachedDriveTree(tree=tree, refreshed_at=row.refreshed_at)

    @override
    async def get_drive_tree_refreshed_at(
        self, db: AsyncSession, drive_id: DriveId
    ) -> datetime | None:
        refreshed_at = await self._load_refreshed_at(db, drive_id)
        await db.commit()
        if (
            refreshed_at is not None
            and datetime.now(UTC) - refreshed_at > settings.SIDEBAR_CACHE_TTL
        ):
            self._schedule_refresh(drive_id)
        return refreshed_at

    @override
    async def get_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        children = await self._sf.do(
            self._singleflight_key("folder", folder_id),
            lambda: self._drive.list_folder_children(folder_id),
        )
        if not isinstance(children, list):
            msg = "Folder singleflight returned a drive tree"
            raise TypeError(msg)
        return children

    @override
    async def invalidate_drive(self, db: AsyncSession, drive_id: DriveId) -> None:
        # A mutation visible to one member can stale every member's principal-
        # scoped view. Delete all rows for this drive; each principal repopulates
        # its own tree with its own Google credentials.
        await db.execute(delete(DriveTreeCache).where(DriveTreeCache.drive_id == drive_id))
        await db.commit()
        _decoded_trees.invalidate_drive(drive_id)

    @override
    async def refresh_drive(self, db: AsyncSession, drive_id: DriveId) -> DriveTree:
        await self.invalidate_drive(db, drive_id)
        tree = await self._sf.do(
            self._singleflight_key("drive", drive_id),
            lambda: self._walk_and_save(drive_id),
        )
        if not isinstance(tree, DriveTree):
            msg = "Drive singleflight returned folder children"
            raise TypeError(msg)
        return tree

    def _schedule_refresh(self, drive_id: DriveId) -> None:
        key = self._singleflight_key("drive", drive_id)
        pending = _background_refreshes.get(key)
        if pending is not None and not pending.done():
            return
        task = asyncio.create_task(self._refresh_in_background(drive_id))
        _background_refreshes[key] = task
        self._bg_tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._bg_tasks.discard(done)
            if _background_refreshes.get(key) is done:
                _background_refreshes.pop(key, None)

        task.add_done_callback(completed)

    async def _refresh_in_background(self, drive_id: DriveId) -> None:
        with provider_refresh_scope():
            outcomes = await asyncio.gather(
                self._sf.do(
                    self._singleflight_key("drive", drive_id),
                    lambda: self._walk_and_save(drive_id),
                ),
                return_exceptions=True,
            )
        outcome = outcomes[0]
        match outcome:
            case Exception():
                logger.warning(
                    "background drive refresh failed error_type={error_type}",
                    error_type=type(outcome).__name__,
                )
            case BaseException():
                raise outcome
            case _:
                pass

    async def _walk_and_save(self, drive_id: DriveId) -> DriveTree:
        tree = await self._drive.walk_drive_folders(drive_id)
        async with self._sessionmaker() as db:
            await self._save_tree(db, tree)
        return tree

    async def _decode_row(self, db: AsyncSession, row: DriveTreeCache) -> DriveTree | None:
        key = (UserId(self._user.id), row.drive_id, row.refreshed_at.isoformat())
        cached = _decoded_trees.get(key)
        if cached is not None:
            return cached

        try:
            tree = await _DECODE_FLIGHTS.do(str(key), lambda: self._decode_tree(row.tree))
        except InvalidToken, ValidationError:
            await db.delete(row)
            await db.commit()
            return None
        _decoded_trees.put(key, tree)
        return tree

    async def _decode_tree(self, blob: str) -> DriveTree:
        return await asyncio.to_thread(_decrypt_and_parse, blob)

    async def _save_tree(self, db: AsyncSession, tree: DriveTree) -> None:
        # Serialization and encryption must not stall the request event loop.
        encrypted = await asyncio.to_thread(_encrypt_tree, tree)
        stmt = (
            pg_insert(DriveTreeCache)
            .values(
                user_id=self._user.id,
                drive_id=tree.drive_id,
                tree=encrypted,
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
