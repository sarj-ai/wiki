from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache, lru_cache
from types import TracebackType
from typing import NamedTuple, override

from cryptography.fernet import InvalidToken, MultiFernet
from loguru import logger
from pydantic import ValidationError
from pydantic_core import to_json
from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    Select,
    Text,
    Uuid,
    and_,
    bindparam,
    case,
    cast,
    delete,
    func,
    literal,
    literal_column,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.auth.encryption import cache_decoder_identity, decrypt_cache_bytes, encrypt_cache_bytes
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
_DECODED_TREE_CACHE_PAYLOAD_BYTE_MAX = 32 * 1024 * 1024
_TRANSACTION_ID_EPOCH_BITS = 32
_INLINE_TREE_DECODE_BYTES = 16 * 1024
_TREE_ETAG_TIMESTAMP_MIN_CHARS = 25
_TREE_ETAG_TIMESTAMP_MAX_CHARS = 32


class _TreeVersion(NamedTuple):
    refreshed_at: datetime
    transaction_epoch: int
    transaction_id: str
    tuple_location: str


class _TreeSnapshot(NamedTuple):
    drive_id: DriveId
    version: _TreeVersion
    tree: str | None


type _DecodedTreeKey = tuple[UserId, DriveId, _TreeVersion, object]


class _DecodedTreeCache:
    def __init__(self, *, max_entries: int, max_nodes: int, max_payload_bytes: int) -> None:
        self._max_entries = max_entries
        self._max_nodes = max_nodes
        self._max_payload_bytes = max_payload_bytes
        self._values: OrderedDict[_DecodedTreeKey, DriveTree] = OrderedDict()
        self._latest: dict[tuple[UserId, DriveId], _DecodedTreeKey] = {}
        self._payload_sizes: dict[_DecodedTreeKey, int] = {}

    def get(self, key: _DecodedTreeKey) -> DriveTree | None:
        tree = self._values.get(key)
        if tree is not None:
            self._values.move_to_end(key)
        return tree

    def latest(
        self, user_id: UserId, drive_id: DriveId, decoder: object
    ) -> tuple[_TreeVersion, DriveTree] | None:
        key = self._latest.get((user_id, drive_id))
        if key is None or key[3] is not decoder:
            return None
        return key[2], self._values[key]

    def put(self, key: _DecodedTreeKey, tree: DriveTree, *, payload_bytes: int) -> None:
        if (
            len(tree.folders) + len(tree.files) > self._max_nodes
            or payload_bytes > self._max_payload_bytes
        ):
            return
        resource = (key[0], key[1])
        if (previous := self._latest.get(resource)) is not None:
            self._values.pop(previous)
            self._payload_sizes.pop(previous)
        self._latest[resource] = key
        self._values[key] = tree
        self._payload_sizes[key] = payload_bytes
        self._values.move_to_end(key)
        while (
            len(self._values) > self._max_entries
            or sum(len(value.folders) + len(value.files) for value in self._values.values())
            > self._max_nodes
            or sum(self._payload_sizes.values()) > self._max_payload_bytes
        ):
            evicted, _ = self._values.popitem(last=False)
            self._latest.pop((evicted[0], evicted[1]))
            self._payload_sizes.pop(evicted)

    def invalidate_drive(self, drive_id: DriveId) -> None:
        for key in [key for key in self._values if key[1] == drive_id]:
            self._values.pop(key)
            self._latest.pop((key[0], key[1]))
            self._payload_sizes.pop(key)


_decoded_trees = _DecodedTreeCache(
    max_entries=_DECODED_TREE_CACHE_MAX,
    max_nodes=_DECODED_TREE_CACHE_NODE_MAX,
    max_payload_bytes=_DECODED_TREE_CACHE_PAYLOAD_BYTE_MAX,
)


def _log_refresh_failure(
    _error_type: type[BaseException] | None,
    error: BaseException | None,
    _traceback: TracebackType | None,
) -> bool:
    if not isinstance(error, Exception):
        return False
    logger.warning(
        "background drive refresh failed error_type={error_type}",
        error_type=type(error).__name__,
    )
    return True


def _decrypt_and_parse(blob: str, decoder: MultiFernet) -> DriveTree:
    return DriveTree.model_validate_json(decrypt_cache_bytes(blob, decoder=decoder))


def _encrypt_tree(tree: DriveTree) -> str:
    return encrypt_cache_bytes(to_json(tree))


@dataclass(frozen=True)
class CachedDriveTree:
    tree: DriveTree
    refreshed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConditionalDriveTree:
    cached: CachedDriveTree | None
    refreshed_at: datetime | None


@dataclass(frozen=True, slots=True)
class _ConditionalTreeSnapshot:
    refreshed_at: datetime
    snapshot: _TreeSnapshot | None


def tree_etag(drive_id: DriveId, refreshed_at: datetime | None) -> str | None:
    if refreshed_at is None:
        return None
    return f'W/"{drive_id}:{refreshed_at.isoformat()}"'


def _requested_tree_time(drive_id: DriveId, request_etag: str) -> datetime | None:
    prefix = f'W/"{drive_id}:'
    if not request_etag.startswith(prefix) or not request_etag.endswith('"'):
        return None
    size = len(request_etag) - len(prefix) - 1
    if not _TREE_ETAG_TIMESTAMP_MIN_CHARS <= size <= _TREE_ETAG_TIMESTAMP_MAX_CHARS:
        return None
    stamp = request_etag[len(prefix) : -1]
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if parsed.utcoffset() != timedelta(0) or parsed.isoformat() != stamp:
        return None
    return parsed


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

    async def get_drive_tree_if_modified(
        self, db: AsyncSession, drive_id: DriveId, *, request_etag: str
    ) -> ConditionalDriveTree:
        refreshed_at = await self.get_drive_tree_refreshed_at(db, drive_id)
        if (etag := tree_etag(drive_id, refreshed_at)) is not None and request_etag == etag:
            return ConditionalDriveTree(None, refreshed_at)
        cached = await self.get_drive_tree(db, drive_id)
        return ConditionalDriveTree(cached, cached.refreshed_at)

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
        decoder = self._cache_decoder()
        decoded = _decoded_trees.latest(UserId(self._user.id), drive_id, decoder)
        row = await self._load_row(db, drive_id, decoded[0] if decoded is not None else None)
        # The request session uses expire_on_commit=False. Release its read
        # transaction before decoding or provider work needs another session.
        await db.commit()
        if row is None:
            return None
        if (tree := await self._reuse_or_decode_tree(db, row, decoded, decoder)) is None:
            return None
        if datetime.now(UTC) - row.version.refreshed_at > settings.SIDEBAR_CACHE_TTL:
            self._schedule_refresh(drive_id)
        return CachedDriveTree(tree=tree, refreshed_at=row.version.refreshed_at)

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
    async def get_drive_tree_if_modified(
        self, db: AsyncSession, drive_id: DriveId, *, request_etag: str
    ) -> ConditionalDriveTree:
        if type(self) is not PostgresSidebarCacheService:
            return await super().get_drive_tree_if_modified(db, drive_id, request_etag=request_etag)
        decoder = self._cache_decoder()
        decoded = _decoded_trees.latest(UserId(self._user.id), drive_id, decoder)
        requested = _requested_tree_time(drive_id, request_etag)
        row = await self._load_conditional_row(
            db, drive_id, decoded[0] if decoded is not None else None, requested
        )
        await db.commit()
        if row is None:
            cached = await self.get_drive_tree(db, drive_id)
            return ConditionalDriveTree(cached, cached.refreshed_at)
        refreshed_at, snapshot = row.refreshed_at, row.snapshot
        if datetime.now(UTC) - refreshed_at > settings.SIDEBAR_CACHE_TTL:
            self._schedule_refresh(drive_id)
        if request_etag == tree_etag(drive_id, refreshed_at):
            return ConditionalDriveTree(None, refreshed_at)
        if snapshot is None:
            cached = await self.get_drive_tree(db, drive_id)
            return ConditionalDriveTree(cached, cached.refreshed_at)
        if (tree := await self._reuse_or_decode_tree(db, snapshot, decoded, decoder)) is None:
            cached = await self.get_drive_tree(db, drive_id)
            return ConditionalDriveTree(cached, cached.refreshed_at)
        return ConditionalDriveTree(CachedDriveTree(tree, refreshed_at), refreshed_at)

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
        # The outer scope handles only ordinary failures after provider refresh
        # ownership resets. Cancellation and other BaseExceptions propagate.
        with ExitStack() as failures, provider_refresh_scope():
            failures.push(_log_refresh_failure)
            await self._sf.do(
                self._singleflight_key("drive", drive_id),
                lambda: self._walk_and_save(drive_id),
            )

    async def _walk_and_save(self, drive_id: DriveId) -> DriveTree:
        tree = await self._drive.walk_drive_folders(drive_id)
        async with self._sessionmaker() as db:
            await self._save_tree(db, tree)
        return tree

    async def _reuse_or_decode_tree(
        self,
        db: AsyncSession,
        row: _TreeSnapshot,
        decoded: tuple[_TreeVersion, DriveTree] | None,
        decoder: MultiFernet,
    ) -> DriveTree | None:
        if row.tree is not None:
            return await self._decode_row(db, row, decoder)
        if decoded is None or decoded[0] != row.version:
            msg = "Sidebar ciphertext omitted without its decoded version"
            raise RuntimeError(msg)
        _decoded_trees.get((UserId(self._user.id), row.drive_id, row.version, decoder))
        return decoded[1]

    async def _decode_row(
        self, db: AsyncSession, row: _TreeSnapshot, decoder: MultiFernet
    ) -> DriveTree | None:
        key = (UserId(self._user.id), row.drive_id, row.version, decoder)
        cached = _decoded_trees.get(key)
        if cached is not None:
            return cached

        blob = row.tree
        if blob is None:
            msg = "Sidebar decode requires ciphertext"
            raise ValueError(msg)
        try:
            tree = await _DECODE_FLIGHTS.do(
                str((*key[:3], id(decoder))), lambda: self._decode_tree(blob, decoder)
            )
        except InvalidToken, ValidationError:
            # A replacement saved during decode owns a new tuple version.
            # Never delete that replacement while discarding a corrupt snapshot.
            await db.execute(
                delete(DriveTreeCache).where(
                    DriveTreeCache.user_id == self._user.id,
                    DriveTreeCache.drive_id == row.drive_id,
                    DriveTreeCache.refreshed_at == row.version.refreshed_at,
                    _tree_transaction_id() == row.version.transaction_id,
                    _tree_tuple_location() == row.version.tuple_location,
                    self._transaction_epoch() == row.version.transaction_epoch,
                )
            )
            await db.commit()
            return None
        # Ciphertext length bounds the original serialized payload retained by
        # these models, including large descriptions on otherwise tiny trees.
        _decoded_trees.put(key, tree, payload_bytes=len(blob))
        return tree

    async def _decode_tree(self, blob: str, decoder: MultiFernet) -> DriveTree:
        if len(blob) <= _INLINE_TREE_DECODE_BYTES:
            return _decrypt_and_parse(blob, decoder)
        return await asyncio.to_thread(_decrypt_and_parse, blob, decoder)

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

    async def _load_row(
        self, db: AsyncSession, drive_id: DriveId, decoded_version: _TreeVersion | None
    ) -> _TreeSnapshot | None:
        parameters: dict[str, object] = {
            "tree_user_id": self._user.id,
            "tree_drive_id": drive_id,
        }
        if decoded_version is not None:
            parameters.update(
                tree_refreshed_at=decoded_version.refreshed_at,
                tree_transaction_id=decoded_version.transaction_id,
                tree_tuple_location=decoded_version.tuple_location,
                tree_transaction_epoch=decoded_version.transaction_epoch,
            )
        row = (
            await db.execute(
                _tree_read_statement(self._transaction_epoch(), warm=decoded_version is not None),
                parameters,
            )
        ).one_or_none()
        if row is None:
            return None
        return _TreeSnapshot(
            drive_id,
            _TreeVersion(
                row.refreshed_at,
                row.transaction_epoch,
                row.transaction_id,
                row.tuple_location,
            ),
            row.tree,
        )

    async def _load_conditional_row(
        self,
        db: AsyncSession,
        drive_id: DriveId,
        decoded_version: _TreeVersion | None,
        requested: datetime | None,
    ) -> _ConditionalTreeSnapshot | None:
        parameters: dict[str, object] = {
            "tree_user_id": self._user.id,
            "tree_drive_id": drive_id,
            "requested_tree_time": requested,
        }
        if decoded_version is not None:
            parameters.update(
                tree_refreshed_at=decoded_version.refreshed_at,
                tree_transaction_id=decoded_version.transaction_id,
                tree_tuple_location=decoded_version.tuple_location,
                tree_transaction_epoch=decoded_version.transaction_epoch,
            )
        row = (
            await db.execute(
                _conditional_tree_read_statement(
                    self._transaction_epoch(), warm=decoded_version is not None
                ),
                parameters,
            )
        ).one_or_none()
        if row is None:
            return None
        if row.transaction_epoch is None:
            return _ConditionalTreeSnapshot(row.refreshed_at, None)
        return _ConditionalTreeSnapshot(
            row.refreshed_at,
            _TreeSnapshot(
                drive_id,
                _TreeVersion(
                    row.refreshed_at, row.transaction_epoch, row.transaction_id, row.tuple_location
                ),
                row.tree,
            ),
        )

    async def _load_refreshed_at(self, db: AsyncSession, drive_id: DriveId) -> datetime | None:
        result = await db.execute(
            _tree_refreshed_statement(),
            {"tree_user_id": self._user.id, "tree_drive_id": drive_id},
        )
        return result.scalar_one_or_none()

    def _singleflight_key(self, kind: str, resource_id: str) -> str:
        return f"user:{self._user.id}:{kind}:{resource_id}"

    def _cache_decoder(self) -> MultiFernet:
        return cache_decoder_identity()

    def _transaction_epoch(self) -> ColumnElement[int]:
        return _tree_transaction_epoch()


@cache
def _tree_transaction_id() -> ColumnElement[str]:
    return cast(literal_column("user_drive_tree_cache.xmin"), Text)


@cache
def _tree_tuple_location() -> ColumnElement[str]:
    return cast(literal_column("user_drive_tree_cache.ctid"), Text)


@cache
def _tree_transaction_epoch() -> ColumnElement[int]:
    # Only this immutable SQL expression is cached; PostgreSQL evaluates the
    # current snapshot afresh for every statement without allocating a xid.
    # A changed epoch invalidates reuse even if a 32-bit xmin wraps and a tuple
    # location is later recycled. Holding the decoder object also prevents its
    # Python identity from being recycled while a decoded tree remains cached.
    snapshot_xmax = cast(cast(func.pg_snapshot_xmax(func.pg_current_snapshot()), Text), BigInteger)
    return snapshot_xmax.op(">>")(literal(_TRANSACTION_ID_EPOCH_BITS, type_=Integer))


@cache
def _tree_refreshed_statement() -> Select[datetime]:
    return select(DriveTreeCache.refreshed_at).where(
        DriveTreeCache.user_id == bindparam("tree_user_id", type_=Uuid),
        DriveTreeCache.drive_id == bindparam("tree_drive_id", type_=Text),
    )


@lru_cache(maxsize=4)
def _tree_read_statement(
    transaction_epoch: ColumnElement[int], *, warm: bool
) -> Select[datetime, int, str, str, str]:
    transaction_id = _tree_transaction_id()
    tuple_location = _tree_tuple_location()
    ciphertext: ColumnElement[str] = cast(DriveTreeCache.tree, Text)
    if warm:
        ciphertext = case(
            (
                and_(
                    DriveTreeCache.refreshed_at
                    == bindparam("tree_refreshed_at", type_=DateTime(timezone=True)),
                    transaction_id == bindparam("tree_transaction_id", type_=Text),
                    tuple_location == bindparam("tree_tuple_location", type_=Text),
                    transaction_epoch == bindparam("tree_transaction_epoch", type_=BigInteger),
                ),
                None,
            ),
            else_=DriveTreeCache.tree,
        )
    return select(
        DriveTreeCache.refreshed_at,
        transaction_epoch.label("transaction_epoch"),
        transaction_id.label("transaction_id"),
        tuple_location.label("tuple_location"),
        ciphertext.label("tree"),
    ).where(
        DriveTreeCache.user_id == bindparam("tree_user_id", type_=Uuid),
        DriveTreeCache.drive_id == bindparam("tree_drive_id", type_=Text),
    )


@lru_cache(maxsize=4)
def _conditional_tree_read_statement(
    transaction_epoch: ColumnElement[int], *, warm: bool
) -> Select[datetime, int, str, str, str]:
    # Exact canonical request timestamps mask both identity work and ciphertext
    # on304. Changed rows retain the same exact decoded-version masking rule.
    unchanged = DriveTreeCache.refreshed_at == bindparam(
        "requested_tree_time", type_=DateTime(timezone=True)
    )
    original = _tree_read_statement(transaction_epoch, warm=warm)
    refreshed_at, epoch, transaction_id, tuple_location, ciphertext = original.selected_columns
    return select(
        refreshed_at,
        case((unchanged, None), else_=epoch).label("transaction_epoch"),
        case((unchanged, None), else_=transaction_id).label("transaction_id"),
        case((unchanged, None), else_=tuple_location).label("tuple_location"),
        case((unchanged, None), else_=ciphertext).label("tree"),
    ).where(
        DriveTreeCache.user_id == bindparam("tree_user_id", type_=Uuid),
        DriveTreeCache.drive_id == bindparam("tree_drive_id", type_=Text),
    )
