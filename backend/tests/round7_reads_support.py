from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self, override
import uuid

from cryptography.fernet import Fernet
from pydantic import JsonValue
from pydantic_core import to_json
from pydantic_settings import BaseSettings
from sqlalchemy import delete
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.encryption import encrypt_cache_bytes
from app.cache.protection import CacheEntryProtector
from app.cache.service import CacheNamespace, NullCacheService
from app.database import Base
from app.drive.schemas import DriveNode, DriveSummary, DriveTree
from app.extras.schemas import DocIconRead, PreferencesRead
from app.extras.service import SidebarExtras
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import CacheEntry, DocumentAlias, DriveTreeCache


class ReadsSettings(BaseSettings):
    wiki_round7_reads_database_url: str | None = None


def reads_url() -> URL | None:
    configured = ReadsSettings().wiki_round7_reads_database_url
    if configured is None:
        return None
    url = make_url(configured)
    assert (url.host, url.port, url.database) == ("127.0.0.1", 15488, "wiki_round7_reads_bench")
    return url


@dataclass
class ReadsDatabase:
    sessions: async_sessionmaker[AsyncSession]
    user: User
    protector: CacheEntryProtector
    rotation_keys: tuple[str, ...] = field(repr=False)
    cache_keys: dict[CacheNamespace, set[str]] = field(default_factory=dict)

    def register_cache(self, namespace: CacheNamespace, values: Mapping[str, JsonValue]) -> None:
        self.cache_keys.setdefault(namespace, set()).update(values)


@asynccontextmanager
async def reads_database(url: URL) -> AsyncGenerator[ReadsDatabase]:
    engine = create_async_engine(url, pool_size=2, max_overflow=0, pool_timeout=2)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = User(
        id=UserId(uuid.uuid7()),
        email=f"round7-reads-{uuid.uuid7()}@example.com",
        google_id=GoogleUserId(f"round7-reads-{uuid.uuid7()}"),
        token_version=0,
    )
    rotation_keys = tuple(Fernet.generate_key().decode() for _ in range(3))
    database = ReadsDatabase(sessions, user, CacheEntryProtector(rotation_keys), rotation_keys)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        db.add(user)
        await db.commit()
    try:
        yield database
    finally:
        async with sessions() as db:
            for namespace, logical_keys in database.cache_keys.items():
                physical = database.protector.physical_keys_many(
                    namespace, list(logical_keys), include_rotation=True
                )
                await db.execute(
                    delete(CacheEntry).where(
                        CacheEntry.namespace == namespace,
                        CacheEntry.key.in_([key for keys in physical.values() for key in keys]),
                    )
                )
            await db.execute(delete(DocumentAlias).where(DocumentAlias.created_by == user.id))
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()
        await engine.dispose()


class ReadsFailureError(RuntimeError):
    pass


class ReadsGate:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.active = False
        self.cancelled = False

    async def enter(self) -> None:
        self.active = True
        self.started.set()
        try:
            await self.release.wait()
            if self.fail:
                msg = "round7 fixture read failed"
                raise ReadsFailureError(msg)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.active = False


class ReadsDrive:
    def __init__(
        self, drives: list[DriveSummary] | None = None, *, gate: ReadsGate | None = None
    ) -> None:
        self.drives = drives or []
        self.gate = gate
        self.calls = 0

    async def list_drives(self) -> list[DriveSummary]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.enter()
        return self.drives


class ReadsDriveCache(NullCacheService):
    def __init__(self, gate: ReadsGate) -> None:
        self.gate = gate
        self.calls = 0

    @override
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None:
        assert namespace == CacheNamespace.DRIVE_LIST
        assert key
        self.calls += 1
        await self.gate.enter()
        return [{"id": "round7-fixture-shared-drive", "name": "Shared fixture drive"}]


class ReadsMembershipDrive(ReadsDrive):
    def __init__(self, member: DriveId | None) -> None:
        super().__init__(
            [
                DriveSummary(
                    id=DriveId("round7-fixture-personal-drive"), name="Fixture personal drive"
                )
            ]
        )
        self.member = member

    @property
    def google_refresh_handler(self) -> None:
        return None

    async def resolve_member_drives(
        self,
        locations: Mapping[FileId, DriveId | None],
        *,
        parent_ids: Mapping[FileId, FileId | None],
    ) -> dict[FileId, DriveId]:
        assert set(parent_ids) == set(locations)
        return dict.fromkeys(locations, self.member) if self.member is not None else {}


@dataclass
class ReadsMetadataRequest:
    response: dict[str, JsonValue]


class ReadsMetadataBatch:
    def __init__(self, callback: Callable[[str, object, BaseException | None], None]) -> None:
        self.callback = callback
        self.requests: list[tuple[str, ReadsMetadataRequest]] = []

    def add(self, request: ReadsMetadataRequest, *, request_id: str) -> None:
        self.requests.append((request_id, request))

    def execute(self) -> None:
        for request_id, request in self.requests:
            self.callback(request_id, request.response, None)


class ReadsMetadataClient:
    def files(self) -> Self:
        return self

    def get(self, **kwargs: str | bool) -> ReadsMetadataRequest:
        return ReadsMetadataRequest(
            {
                "id": str(kwargs["fileId"]),
                "name": "Fixture personal document",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["round7-fixture-personal-root"],
            }
        )

    def new_batch_http_request(
        self, *, callback: Callable[[str, object, BaseException | None], None]
    ) -> ReadsMetadataBatch:
        return ReadsMetadataBatch(callback)


class ReadsExtras:
    def __init__(
        self, icons: list[DocIconRead] | None = None, *, gate: ReadsGate | None = None
    ) -> None:
        self.icons = icons or []
        self.gate = gate

    async def list_doc_icons(self) -> list[DocIconRead]:
        return self.icons

    async def load_sidebar(self) -> SidebarExtras:
        if self.gate is not None:
            await self.gate.enter()
        return SidebarExtras(
            PreferencesRead(show_non_doc_files=True, hidden_workspace_ids=[]), [], []
        )


class ReadsFiles:
    def __init__(self, files: list[FileResponse] | None = None) -> None:
        self.files = {file.id: file for file in files or []}
        self.bulk_calls: list[list[FileId]] = []
        self.access_calls: list[FileId] = []
        self.icon_calls: list[list[FileId]] = []

    async def get_search_metadata_many(self, ids: list[FileId]) -> dict[FileId, FileResponse]:
        self.bulk_calls.append(ids)
        return {file_id: self.files[file_id] for file_id in ids if file_id in self.files}

    async def require_access(self, file_id: FileId) -> None:
        self.access_calls.append(file_id)
        if file_id not in self.files:
            msg = "round7 fixture file unavailable"
            raise FileNotInScopeError(msg)

    async def get_accessible_ids(self, ids: list[FileId]) -> set[FileId]:
        self.icon_calls.append(ids)
        return set(ids) & self.files.keys()


def reads_file(file_id: FileId, *, drive_id: DriveId | None = None) -> FileResponse:
    return FileResponse(
        id=file_id,
        name="Round7 fixture document",
        mime_type="application/vnd.google-apps.document",
        drive_id=drive_id,
        parent_folder_id=None,
        modified_time=None,
    )


async def seed_alias(database: ReadsDatabase, file_id: FileId) -> str:
    alias = "fixture-" + "".join(chr(97 + int(digit, 16)) for digit in uuid.uuid7().hex[-12:])
    async with database.sessions() as db:
        db.add(DocumentAlias(alias=alias, file_id=file_id, created_by=database.user.id))
        await db.commit()
    return alias


async def seed_trees(
    database: ReadsDatabase, *, count: int, description_bytes: int
) -> list[DriveId]:
    drives = [DriveId(f"round7-fixture-{database.user.id}-{index}") for index in range(count)]
    async with database.sessions() as db:
        for drive in drives:
            tree = DriveTree(
                drive_id=drive,
                folders=[],
                files=[
                    DriveNode(
                        id=FileId(f"{drive}-doc"),
                        name="Round7 fixture document",
                        mime_type="text/plain",
                        parent_id=FileId(drive),
                        modified_time=None,
                        description="r" * description_bytes,
                    )
                ],
                files_complete=True,
            )
            db.add(
                DriveTreeCache(
                    user_id=database.user.id,
                    drive_id=drive,
                    tree=encrypt_cache_bytes(to_json(tree)),
                    refreshed_at=datetime.now(UTC),
                    refreshed_by=database.user.id,
                )
            )
        await db.commit()
    return drives
