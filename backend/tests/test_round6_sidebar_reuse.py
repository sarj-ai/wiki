from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import get_ident
from types import TracebackType
from typing import NamedTuple, Self, override
import uuid

from cryptography.fernet import Fernet, MultiFernet
from loguru import logger
from pydantic_core import to_json
from sqlalchemy import Select
from sqlalchemy.sql import ClauseElement

from app.drive.schemas import DriveNode, DriveTree
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from app.sidebar.cache import PostgresSidebarCacheService
from app.sidebar.singleflight import Singleflight
from tests.support import dependency


class _Snapshot(NamedTuple):
    refreshed_at: datetime
    transaction_epoch: int
    transaction_id: str
    tuple_location: str
    tree: str | None


class _Result:
    def __init__(self, row: _Snapshot | None = None) -> None:
        self.row = row

    def one_or_none(self) -> _Snapshot | None:
        return self.row


class _TreeSession:
    def __init__(self) -> None:
        self.rows: dict[tuple[UserId, DriveId], _Snapshot] = {}
        self.executions: list[tuple[ClauseElement, dict[str, object]]] = []
        self.commits = 0

    async def execute(
        self, statement: ClauseElement, parameters: dict[str, object] | None = None
    ) -> _Result:
        bound = dict(parameters or {})
        self.executions.append((statement, bound))
        if isinstance(statement, Select):
            resource = (dependency(bound["tree_user_id"]), dependency(bound["tree_drive_id"]))
            row = self.rows.get(resource)
            if (
                row is not None
                and (
                    bound.get("tree_refreshed_at"),
                    bound.get("tree_transaction_epoch"),
                    bound.get("tree_transaction_id"),
                    bound.get("tree_tuple_location"),
                )
                == row[:4]
            ):
                row = row._replace(tree=None)
            return _Result(row)
        compiled = statement.compile()
        for resource in [
            key for key in self.rows if key[1] == dependency(compiled.params)["drive_id_1"]
        ]:
            self.rows.pop(resource)
        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None


class _ObservedDecoder(MultiFernet):
    def __init__(self, fernet: Fernet) -> None:
        super().__init__([fernet])
        self.threads: list[int] = []

    @override
    def decrypt(self, msg: bytes | str, ttl: int | None = None) -> bytes:
        self.threads.append(get_ident())
        return super().decrypt(msg, ttl=ttl)


class _SidebarService(PostgresSidebarCacheService):
    def __init__(
        self,
        db: _TreeSession,
        user: User,
        decoder: _ObservedDecoder,
        *,
        drive: object | None = None,
        bg_tasks: set[asyncio.Task[None]] | None = None,
    ) -> None:
        super().__init__(
            drive=dependency(drive),
            user=user,
            singleflight=Singleflight(),
            bg_tasks=bg_tasks if bg_tasks is not None else set(),
            sessionmaker=dependency(lambda: db),
        )
        self.decoder = decoder

    @override
    def _cache_decoder(self) -> MultiFernet:
        return self.decoder


def _user() -> User:
    return User(
        id=UserId(uuid.uuid7()),
        email="round6-sidebar@example.com",
        google_id=GoogleUserId("round6-sidebar"),
        token_version=0,
    )


def _tree(drive_id: DriveId, *, content: str = "") -> DriveTree:
    return DriveTree(
        drive_id=drive_id,
        folders=[],
        files=[
            DriveNode(
                id=FileId("doc"),
                name="Document",
                mime_type="text/plain",
                parent_id=FileId(drive_id),
                modified_time=None,
                description=content,
            )
        ],
        files_complete=True,
    )


def _snapshot(fernet: Fernet, tree: DriveTree, *, transaction: str = "1") -> _Snapshot:
    return _Snapshot(
        datetime.now(UTC), 0, transaction, "(0,1)", fernet.encrypt(to_json(tree)).decode()
    )


async def test_warm_tree_statement_binds_fresh_owner_drive_and_exact_version() -> None:
    db = _TreeSession()
    fernet = Fernet(Fernet.generate_key())
    decoder = _ObservedDecoder(fernet)
    owner, other_owner = _user(), _user()
    drive = DriveId(f"round6-{uuid.uuid7()}")
    owner_tree, other_tree = _tree(drive, content="owner"), _tree(drive, content="other")
    db.rows[owner.id, drive] = _snapshot(fernet, owner_tree)
    db.rows[other_owner.id, drive] = _snapshot(fernet, other_tree)
    services = [_SidebarService(db, user, decoder) for user in [owner, other_owner]]
    for service, expected in zip(services, [owner_tree, other_tree], strict=True):
        cold = await service.peek_drive_tree(dependency(db), drive)
        warm = await service.peek_drive_tree(dependency(db), drive)
        assert cold is not None
        assert warm is not None
        assert cold.tree == warm.tree == expected
    first_warm, other_warm = db.executions[1], db.executions[3]
    assert first_warm[0] is other_warm[0]
    assert first_warm[1]["tree_user_id"] != other_warm[1]["tree_user_id"]
    assert first_warm[1]["tree_transaction_id"] == "1"
    assert "pg_current_snapshot" in str(first_warm[0])
    assert len(decoder.threads) == 2
    replacement = _tree(drive, content="replacement")
    db.rows[owner.id, drive] = _snapshot(fernet, replacement, transaction="2")
    updated = await services[0].peek_drive_tree(dependency(db), drive)
    assert updated is not None
    assert updated.tree == replacement
    assert len(decoder.threads) == 3
    await services[0].invalidate_drive(dependency(db), drive)
    assert await services[1].peek_drive_tree(dependency(db), drive) is None
    assert db.commits == len(db.executions)


async def test_small_tree_decode_is_inline_and_large_ciphertext_uses_a_worker() -> None:
    db = _TreeSession()
    user = _user()
    fernet = Fernet(Fernet.generate_key())
    decoder = _ObservedDecoder(fernet)
    service = _SidebarService(db, user, decoder)
    for description in ["small", "📚" * (32 * 1024)]:
        drive = DriveId(f"round6-decode-{uuid.uuid7()}")
        expected = _tree(drive, content=description)
        db.rows[user.id, drive] = _snapshot(fernet, expected)
        actual = await service.peek_drive_tree(dependency(db), drive)
        assert actual is not None
        assert actual.tree == expected
    assert decoder.threads[0] == get_ident()
    assert decoder.threads[1] != get_ident()


async def test_decoded_latest_index_tracks_entry_eviction_and_old_version_replacement() -> None:
    db = _TreeSession()
    user = _user()
    fernet = Fernet(Fernet.generate_key())
    decoder = _ObservedDecoder(fernet)
    service = _SidebarService(db, user, decoder)
    drives = [DriveId(f"round6-eviction-{uuid.uuid7()}") for _ in range(33)]
    for drive in drives:
        db.rows[user.id, drive] = _snapshot(fernet, _tree(drive))
        assert await service.peek_drive_tree(dependency(db), drive) is not None
    assert len(decoder.threads) == 33
    assert await service.peek_drive_tree(dependency(db), drives[-1]) is not None
    assert len(decoder.threads) == 33
    assert await service.peek_drive_tree(dependency(db), drives[0]) is not None
    assert len(decoder.threads) == 34
    replacement = _tree(drives[0], content="new version")
    db.rows[user.id, drives[0]] = _snapshot(fernet, replacement, transaction="changed")
    cached = await service.peek_drive_tree(dependency(db), drives[0])
    assert cached is not None
    assert cached.tree == replacement
    assert len(decoder.threads) == 35
    assert await service.peek_drive_tree(dependency(db), drives[0]) is not None
    assert len(decoder.threads) == 35


class _OverlayResult:
    def all(self) -> list[tuple[str, object, object, object, object, object]]:
        return []


class _OverlaySession:
    def __init__(self) -> None:
        self.executions: list[tuple[ClauseElement, dict[str, object]]] = []

    async def execute(
        self, statement: ClauseElement, parameters: dict[str, object]
    ) -> _OverlayResult:
        self.executions.append((statement, parameters))
        return _OverlayResult()


async def test_sidebar_overlay_shape_binds_current_user_per_public_service() -> None:
    db = _OverlaySession()
    users = [_user(), _user()]
    await asyncio.gather(
        *(PostgresExtrasService(db=dependency(db), user=user).load_sidebar() for user in users)
    )
    first, second = db.executions
    assert first[0] is second[0]
    assert first[1]["sidebar_user_id"] == users[0].id
    assert second[1]["sidebar_user_id"] == users[1].id
    assert str(first[0]).count(":sidebar_user_id") == 3


class _ProviderInterruption(BaseException):
    pass


class _BackgroundDrive:
    def __init__(self, *, fail: bool, interrupted: bool = False) -> None:
        self.fail = fail
        self.interrupted = interrupted
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def walk_drive_folders(self, _drive: DriveId) -> DriveTree:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.interrupted:
            raise _ProviderInterruption
        if self.fail:
            msg = "private-provider-response-that-must-not-be-logged"
            raise RuntimeError(msg)
        msg = "This fixture always cancels successful provider work before saving"
        raise AssertionError(msg)


async def test_direct_background_refresh_preserves_redacted_failure_logging() -> None:
    db = _TreeSession()
    user = _user()
    fernet = Fernet(Fernet.generate_key())
    decoder = _ObservedDecoder(fernet)
    drive = DriveId(f"round6-failed-background-{uuid.uuid7()}")
    row = _snapshot(fernet, _tree(drive))
    db.rows[user.id, drive] = row._replace(refreshed_at=row.refreshed_at.replace(year=2020))
    provider = _BackgroundDrive(fail=True)
    bg_tasks: set[asyncio.Task[None]] = set()
    service = _SidebarService(db, user, decoder, drive=provider, bg_tasks=bg_tasks)
    messages: list[object] = []
    sink = logger.add(messages.append, format="{message}")
    try:
        assert await service.peek_drive_tree(dependency(db), drive) is not None
        await provider.started.wait()
        tasks = list(bg_tasks)
        provider.release.set()
        await asyncio.gather(*tasks)
        assert all(task.done() and not task.cancelled() for task in tasks)
    finally:
        logger.remove(sink)
    assert len(messages) == 1
    assert "RuntimeError" in str(messages[0])
    assert "private-provider-response" not in str(messages[0])
    assert dependency(messages[0]).record["exception"] is None
    assert not bg_tasks


async def test_direct_background_refresh_propagates_cancellation_and_allows_retry() -> None:
    db = _TreeSession()
    user = _user()
    fernet = Fernet(Fernet.generate_key())
    decoder = _ObservedDecoder(fernet)
    drive = DriveId(f"round6-cancelled-background-{uuid.uuid7()}")
    row = _snapshot(fernet, _tree(drive))
    db.rows[user.id, drive] = row._replace(refreshed_at=row.refreshed_at.replace(year=2020))
    provider = _BackgroundDrive(fail=False)
    bg_tasks: set[asyncio.Task[None]] = set()
    service = _SidebarService(db, user, decoder, drive=provider, bg_tasks=bg_tasks)
    for expected in [1, 2]:
        provider.started.clear()
        assert await service.peek_drive_tree(dependency(db), drive) is not None
        await provider.started.wait()
        tasks = list(bg_tasks)
        assert len(tasks) == 1
        tasks[0].cancel()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert provider.calls == expected
        assert not bg_tasks


async def test_direct_background_refresh_propagates_non_exception_interruption() -> None:
    db = _TreeSession()
    user = _user()
    fernet = Fernet(Fernet.generate_key())
    decoder = _ObservedDecoder(fernet)
    drive = DriveId(f"round6-interrupted-background-{uuid.uuid7()}")
    row = _snapshot(fernet, _tree(drive))
    db.rows[user.id, drive] = row._replace(refreshed_at=row.refreshed_at.replace(year=2020))
    provider = _BackgroundDrive(fail=False, interrupted=True)
    bg_tasks: set[asyncio.Task[None]] = set()
    service = _SidebarService(db, user, decoder, drive=provider, bg_tasks=bg_tasks)
    assert await service.peek_drive_tree(dependency(db), drive) is not None
    await provider.started.wait()
    tasks = list(bg_tasks)
    provider.release.set()
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(outcomes[0], _ProviderInterruption)
    assert not bg_tasks
