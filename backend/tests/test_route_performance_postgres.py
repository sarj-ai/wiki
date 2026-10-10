from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import uuid

from fastapi import Response
import httpx
from pydantic import JsonValue
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.auth.encryption import encrypt_text
from app.cache.service import CacheNamespace, CachePSqlService
from app.drive.dependencies import drive_service_dep
from app.drive.schemas import DriveSummary
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, GoogleUserId, UserId
from app.models.user import User
from app.models.wiki import CacheEntry, UserFolderIcon, UserPreferences, UserSortOrder
from tests.test_task_source_dedupe_postgres import _qa_engine


async def test_drive_reads_release_connection_before_cache_checkout() -> None:
    validated_engine = _qa_engine()
    engine = create_async_engine(validated_engine.url, pool_size=1, max_overflow=0, pool_timeout=1)
    await validated_engine.dispose()
    # One connection proves that provider work cannot depend on the auth
    # transaction retaining a second connection for an independent cache read.
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    user = User(
        id=owner,
        email=f"perf-{owner}@sarj.ai",
        google_id=GoogleUserId(f"perf-{owner}"),
        token_version=0,
        encrypted_access_token=encrypt_text("offline-access"),
        encrypted_refresh_token=encrypt_text("offline-refresh"),
        google_access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        google_token_generation=0,
    )
    cache = CachePSqlService(sessions)
    drives = [DriveSummary(id=DriveId("offline-drive"), name="Offline drive")]
    try:
        async with sessions() as db:
            db.add(user)
            await db.commit()
        await cache.put(
            CacheNamespace.DRIVE_LIST,
            str(owner),
            [drive.model_dump(mode="json") for drive in drives],
            ttl=timedelta(seconds=30),
        )
        async with sessions() as db, httpx.AsyncClient() as http:
            current = await db.get(User, owner)
            assert current is not None
            service = await drive_service_dep(current, db, http, cache, Response())
            assert not db.in_transaction()
            assert isinstance(engine.pool, AsyncAdaptedQueuePool)
            assert engine.pool.checkedout() == 0
            assert await asyncio.wait_for(service.list_drives(), timeout=2) == drives
            assert not db.in_transaction()
    finally:
        await cache.delete(CacheNamespace.DRIVE_LIST, str(owner))
        async with sessions() as db:
            await db.execute(delete(User).where(User.id == owner))
            await db.commit()
        await engine.dispose()


async def test_sidebar_overlays_use_one_user_scoped_database_query() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owners = [UserId(uuid.uuid7()), UserId(uuid.uuid7())]
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

    try:
        async with sessions() as db:
            for owner in owners:
                db.add(
                    User(
                        id=owner,
                        email=f"perf-{owner}@sarj.ai",
                        google_id=GoogleUserId(str(owner)),
                        token_version=0,
                    )
                )
            await db.flush()
            for owner in owners:
                db.add_all(
                    [
                        UserPreferences(
                            user_id=owner,
                            show_non_doc_files=False,
                            hidden_workspace_ids=["hidden-drive"],
                        ),
                        UserFolderIcon(user_id=owner, folder_drive_id=str(owner), icon="📚"),
                        UserSortOrder(user_id=owner, drive_id=str(owner), sort_order=1.5),
                    ]
                )
            await db.commit()
        async with sessions() as db:
            user = await db.get(User, owners[0])
            assert user is not None
            event.listen(engine.sync_engine, "before_cursor_execute", record)
            snapshot = await PostgresExtrasService(db=db, user=user).load_sidebar()
            event.remove(engine.sync_engine, "before_cursor_execute", record)
            assert len(statements) == 1
            assert snapshot.preferences.hidden_workspace_ids == ["hidden-drive"]
            assert [icon.folder_drive_id for icon in snapshot.folder_icons] == [str(owners[0])]
            assert [item.drive_id for item in snapshot.sort_orders] == [str(owners[0])]
            assert snapshot.sort_orders[0].sort_order == 1.5
    finally:
        async with sessions() as db:
            await db.execute(delete(User).where(User.id.in_(owners)))
            await db.commit()
        await engine.dispose()


async def test_large_cache_batch_stays_within_asyncpg_parameter_limit() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"perf-{uuid.uuid7()}"
    values: dict[str, JsonValue] = {
        f"{prefix}-{index}": {"accessible": True} for index in range(10_000)
    }
    cache = CachePSqlService(sessions)
    try:
        await cache.put_many(CacheNamespace.SHORTCUT_PROBE, values, ttl=timedelta(seconds=30))
        assert await cache.get_many(CacheNamespace.SHORTCUT_PROBE, list(values)) == values
    finally:
        async with sessions() as db:
            keys = [
                cache._protector.physical_keys(CacheNamespace.SHORTCUT_PROBE, key)[0]
                for key in values
            ]
            await db.execute(delete(CacheEntry).where(CacheEntry.key.in_(keys)))
            await db.commit()
        await engine.dispose()
