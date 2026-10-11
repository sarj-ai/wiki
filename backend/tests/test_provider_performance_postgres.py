from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple, override
import uuid

import httpx
import pytest
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.aliases.service import PostgresAliasService
from app.auth.encryption import encrypt_text
from app.auth.google_tokens import GoogleTokenManager
from app.cache.service import CacheNamespace, CachePSqlService
from app.drive.google_schemas import GoogleFileAccessLocation
from app.drive.schemas import DriveSummary, DriveTree
from app.drive.service import GoogleDriveService
from app.files.schemas import FileResponse
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import DocumentAlias
from app.sidebar.cache import PostgresSidebarCacheService
from app.sidebar.singleflight import Singleflight
from tests.test_provider_hot_paths import _MetadataFiles
from tests.test_provider_round3 import _CoordinatedBatchProvider, _CountingDrive
from tests.test_task_source_dedupe_postgres import _qa_engine


class _ProviderDatabase(NamedTuple):
    sessions: async_sessionmaker[AsyncSession]
    owner: UserId


class _OfflineDrive(GoogleDriveService):
    def __init__(self, user: User, cache: CachePSqlService | None = None) -> None:
        super().__init__(user, cache)
        self.drive_id = DriveId(f"provider-drive-{user.id}")

    @override
    def _list_drives_sync(self) -> list[DriveSummary]:
        return [DriveSummary(id=self.drive_id, name="Offline drive")]

    @override
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
        return DriveTree(drive_id=drive_id, folders=[], files=[], files_complete=True)


class _OfflineFiles(GoogleFilesService):
    def __init__(self, *, user: User, drive: _OfflineDrive) -> None:
        super().__init__(user=user, drive=drive)
        self._offline_drive_id = drive.drive_id

    @override
    def _get_sync(self, file_id: FileId) -> FileResponse:
        return FileResponse(
            id=file_id,
            name="Offline document",
            mime_type="application/vnd.google-apps.document",
            drive_id=self._offline_drive_id,
            parent_folder_id=None,
            modified_time=None,
        )

    @override
    def _get_access_location_sync(self, file_id: FileId) -> GoogleFileAccessLocation:
        return GoogleFileAccessLocation.model_validate(
            {"id": file_id, "driveId": self._offline_drive_id}
        )

    @override
    def _get_accessible_many_sync(
        self, file_ids: list[FileId], *, fields: str = ""
    ) -> dict[FileId, FileResponse]:
        return {file_id: self._get_sync(file_id) for file_id in file_ids}


@pytest.fixture
async def provider_database() -> AsyncGenerator[_ProviderDatabase]:
    validated = _qa_engine()
    engine = create_async_engine(validated.url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    await validated.dispose()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    try:
        async with sessions() as db:
            db.add(
                User(
                    id=owner,
                    email=f"provider-perf-{owner}@sarj.ai",
                    google_id=GoogleUserId(f"provider-perf-{owner}"),
                    token_version=0,
                    encrypted_access_token=encrypt_text("offline-access"),
                    google_access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
                    google_token_generation=0,
                )
            )
            await db.commit()
        yield _ProviderDatabase(sessions, owner)
    finally:
        await CachePSqlService(sessions).delete(CacheNamespace.DRIVE_LIST, str(owner))
        async with sessions() as db:
            await db.execute(delete(DocumentAlias).where(DocumentAlias.created_by == owner))
            await db.execute(delete(User).where(User.id == owner))
            await db.commit()
        await engine.dispose()


async def test_valid_credential_preparation_selects_fresh_token_without_update(
    provider_database: _ProviderDatabase,
) -> None:
    statements: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    async with provider_database.sessions() as db, httpx.AsyncClient() as http:
        user = await db.get(User, provider_database.owner)
        assert user is not None
        original_ciphertext = user.encrypted_access_token
        bind = db.bind
        assert bind is not None
        event.listen(bind.sync_engine, "before_cursor_execute", record)
        try:
            prepared = await GoogleTokenManager(db, http).prepare(user)
            await db.commit()
        finally:
            event.remove(bind.sync_engine, "before_cursor_execute", record)
        assert prepared.user.encrypted_access_token == original_ciphertext
        assert len(statements) == 1
        assert statements[0].lstrip().startswith("SELECT")


async def test_cancelled_metadata_waiter_preserves_running_provider_refresh(
    provider_database: _ProviderDatabase,
) -> None:
    refreshes = 0
    refreshed_tokens: list[str] = []
    provider = _CoordinatedBatchProvider()
    workers: list[asyncio.Task[object]] = []

    def oauth_response(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        assert request.url.host == "oauth2.googleapis.com"
        refreshes += 1
        return httpx.Response(
            200, json={"access_token": "metadata-worker-refreshed", "expires_in": 3600}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(oauth_response)) as http:

        async def request() -> None:
            async with provider_database.sessions() as db:
                user = await db.get(User, provider_database.owner)
                assert user is not None
                user.encrypted_refresh_token = encrypt_text("metadata-worker-refresh-fixture")
                await db.commit()
                prepared = await GoogleTokenManager(db, http).prepare(user)
                await db.commit()

                def refresh() -> None:
                    refreshed_tokens.append(prepared.refresh_handler(object()).token)

                provider.refresh = refresh
                files = _MetadataFiles(_CountingDrive(), provider)
                try:
                    await files.get_accessible_ids(list(provider.records))
                finally:
                    workers.extend(files._metadata_reads)

        owner = asyncio.create_task(request())
        try:
            await asyncio.wait_for(provider.started.wait(), timeout=2)
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert provider.active == 4
            assert not refreshed_tokens
        finally:
            provider.release.set()
        assert workers
        await asyncio.wait_for(asyncio.gather(*workers), timeout=4)
    assert refreshed_tokens == ["metadata-worker-refreshed"] * 4
    assert refreshes == 1
    assert provider.active == 0
    assert len(provider.batch_sizes) == 4


async def test_cold_sidebar_load_releases_read_connection_before_saving_tree(
    provider_database: _ProviderDatabase,
) -> None:
    drive_id = DriveId(f"provider-perf-{provider_database.owner}")
    tree = DriveTree(drive_id=drive_id, folders=[], files=[], files_complete=True)
    async with provider_database.sessions() as db:
        user = await db.get(User, provider_database.owner)
        assert user is not None
        service = PostgresSidebarCacheService(
            drive=_OfflineDrive(user),
            user=user,
            singleflight=Singleflight(),
            bg_tasks=set(),
            sessionmaker=provider_database.sessions,
        )
        cached = await asyncio.wait_for(service.get_drive_tree(db, drive_id), timeout=2)
        assert cached.tree == tree
        assert not db.in_transaction()


@pytest.mark.parametrize("recent", [False, True])
async def test_alias_reads_release_connection_before_provider_cache_checkout(
    provider_database: _ProviderDatabase,
    *,
    recent: bool,
) -> None:
    file_id = FileId(f"provider-file-{provider_database.owner}")
    suffix = "".join(chr(ord("a") + byte % 26) for byte in provider_database.owner.bytes)
    alias = f"perf-{suffix[:8]}-{suffix[8:]}"
    async with provider_database.sessions() as db:
        db.add(DocumentAlias(alias=alias, file_id=file_id, created_by=provider_database.owner))
        await db.commit()

    async with provider_database.sessions() as db:
        user = await db.get(User, provider_database.owner)
        assert user is not None
        drive = _OfflineDrive(user, CachePSqlService(provider_database.sessions))
        service = PostgresAliasService(
            db=db, files=_OfflineFiles(user=user, drive=drive), user=user, drive=drive
        )
        if recent:
            result = await asyncio.wait_for(service.resolve_recent([alias]), timeout=2)
            assert [item.file_id for item in result] == [file_id]
        else:
            resolved = await asyncio.wait_for(service.resolve(alias), timeout=2)
            assert resolved.file_id == file_id
        assert not db.in_transaction()
