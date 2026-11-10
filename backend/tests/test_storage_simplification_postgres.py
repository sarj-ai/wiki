from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import ARRAY, Column, DateTime, Integer, MetaData, Table, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.extras.schemas import FolderIconWrite, PreferencesWrite, SortOrderWrite
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, FileId
from app.mcp.native_admission import PostgresRegistrationAdmission, RegistrationAdmissionDeniedError
from app.models.mcp_oauth import MCPOAuthEntry
from app.models.user import User
from app.models.wiki import CacheEntry
from tests.test_task_source_dedupe_postgres import _qa_engine


_BACKEND = Path(__file__).parents[1]


def _upgrade(dsn: str, revision: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=_BACKEND,
        env=os.environ | {"DATABASE_URL": dsn},  # ruff: ignore[banned-api] — preserve the subprocess toolchain environment.
        check=True,
        capture_output=True,
        text=True,
    )


async def test_upgrade_preserves_settings_tokens_and_concurrent_user_edits() -> None:
    admin = _qa_engine()
    name = f"wiki_polish29_qa_storage_{uuid.uuid7().hex}"
    url = admin.url.set(database=name)
    dsn = url.render_as_string(hide_password=False)
    engine = create_async_engine(url)
    owner = uuid.uuid7()
    other = uuid.uuid7()
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f'CREATE DATABASE "{name}"')
            )  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
        await asyncio.to_thread(_upgrade, dsn, "0024_configurable_email_domain")
        async with engine.begin() as db:
            await db.execute(  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
                text("INSERT INTO users (id,email,google_id) VALUES (:id,:email,:google)"),
                [
                    {"id": owner, "email": "owner@sarj.ai", "google": "owner"},
                    {"id": other, "email": "other@sarj.ai", "google": "other"},
                ],
            )
            await db.execute(  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
                text(
                    "INSERT INTO user_preferences (user_id,show_non_doc_files,hidden_workspace_ids) VALUES (:id,false,ARRAY['hidden'])"
                ),
                {"id": owner},
            )
            await db.execute(  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
                text(
                    "INSERT INTO user_folder_icons (user_id,folder_drive_id,icon) VALUES (:id,'nullable',NULL)"
                ),
                {"id": owner},
            )
            await db.execute(  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
                text(
                    "INSERT INTO user_sort_order (user_id,drive_id,sort_order) VALUES (:id,'drive',-0.5)"
                ),
                {"id": owner},
            )
            await db.execute(  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
                text(
                    "INSERT INTO mcp_native_state (purpose,key_digest,ciphertext,expires_at) VALUES ('code','digest','unchanged ciphertext',now()+interval '1 hour')"
                )
            )
            await db.execute(  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
                text(
                    "INSERT INTO tasks (id,owner_user_id,kind,status,idempotency_key,request_hash,payload,expires_at) VALUES (:id,:owner,'file.document_transform','pending',:key,:hash,CAST(:payload AS jsonb),now()+interval '1 day')"
                ),
                [
                    {
                        "id": uuid.uuid7(),
                        "owner": owner,
                        "key": uuid.uuid7(),
                        "hash": "a" * 64,
                        "payload": '{"qa_contract":"owner_only_v1"}',
                    },
                    {
                        "id": uuid.uuid7(),
                        "owner": owner,
                        "key": uuid.uuid7(),
                        "hash": "b" * 64,
                        "payload": "{}",
                    },
                ],
            )
            admission = Table(
                "mcp_registration_admission",
                MetaData(),
                Column("id", Integer),
                Column("admitted_at", ARRAY(DateTime(timezone=True))),
            )
            await db.execute(insert(admission).values(id=1, admitted_at=[datetime.now(UTC)] * 10))
            await db.execute(
                insert(MCPOAuthEntry).values(
                    collection="mcp-authorization-codes",
                    key_digest="old-proxy",
                    ciphertext="retired",
                )
            )
            await db.execute(
                insert(CacheEntry).values(
                    namespace="drive.list",
                    key="legacy",
                    value={},
                    expires_at=datetime.now(UTC).replace(year=2030),
                )
            )
        await asyncio.to_thread(_upgrade, dsn, "head")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        with pytest.raises(RegistrationAdmissionDeniedError):
            await PostgresRegistrationAdmission(sessions).admit()
        async with sessions() as db:
            user = await db.get(User, owner)
            default_user = await db.get(User, other)
            assert user is not None
            assert default_user is not None
            state = await PostgresExtrasService(db=db, user=user).load_sidebar()
            assert state.preferences.hidden_workspace_ids == ["hidden"]
            assert not state.preferences.show_non_doc_files
            assert state.folder_icons[0].folder_drive_id == "nullable"
            assert state.folder_icons[0].icon is None
            assert state.sort_orders[0].sort_order == -0.5
            defaults = await PostgresExtrasService(db=db, user=default_user).load_sidebar()
            assert defaults.preferences.show_non_doc_files
            assert defaults.folder_icons == defaults.sort_orders == []
            assert (
                await db.scalar(
                    text(
                        "SELECT ciphertext FROM mcp_oauth_entries WHERE collection='native:code' AND key_digest='digest'"
                    )
                )
                == "unchanged ciphertext"
            )
            assert await db.scalar(text("SELECT count(*) FROM tasks")) == 1
            assert list(await db.scalars(select(MCPOAuthEntry.collection))) == ["native:code"]
            assert list(await db.scalars(select(CacheEntry.namespace))) == ["mcp.registration"]
            assert (
                await db.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'"
                    )
                )
                == 12
            )

        async def write(index: int) -> None:
            async with sessions() as db:
                user = await db.get(User, owner)
                assert user is not None
                service = PostgresExtrasService(db=db, user=user)
                await service.upsert_folder_icon(
                    FileId(f"folder-{index}"), FolderIconWrite(icon="📚")
                )
                await service.upsert_sort_orders(
                    [SortOrderWrite(drive_id=DriveId(f"drive-{index}"), sort_order=index)]
                )
                if index == 0:
                    await service.upsert_preferences(PreferencesWrite(show_non_doc_files=True))

        await asyncio.gather(*(write(index) for index in range(8)))
        async with sessions() as db:
            user = await db.get(User, owner)
            assert user is not None
            assert len(user.folder_icons) == len(user.sort_orders) == 9
            assert user.folder_icons[FileId("nullable")] is None
            assert user.show_non_doc_files
            assert user.hidden_workspace_ids == ["hidden"]
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            )  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
        await admin.dispose()


async def test_google_token_invariants_preserve_refresh_and_reject_contradictions() -> None:
    admin = _qa_engine()
    name = f"wiki_polish29_qa_credentials_{uuid.uuid7().hex}"
    url = admin.url.set(database=name)
    dsn = url.render_as_string(hide_password=False)
    engine = create_async_engine(url)
    owner = uuid.uuid7()
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f'CREATE DATABASE "{name}"')
            )  # sarj-noqa: SARJ429 -- Exercise actual historical DDL in a disposable migration database.
        await asyncio.to_thread(_upgrade, dsn, "0026_retire_oauth_proxy")
        async with engine.begin() as db:
            await db.execute(  # sarj-noqa: SARJ429 -- Seed a valid legacy credential shape before migration.
                text(
                    "INSERT INTO users (id,email,google_id,encrypted_access_token,encrypted_refresh_token,google_token_generation,token_version) VALUES (:id,'credential@example.com','credential','unusable-access','retained-refresh',3,4)"
                ),
                {"id": owner},
            )
        await asyncio.to_thread(_upgrade, dsn, "head")
        async with engine.connect() as db:
            row = (
                await db.execute(  # sarj-noqa: SARJ429 -- Verify exact migration data preservation independently of the new ORM.
                    text(
                        "SELECT encrypted_access_token,encrypted_refresh_token,google_token_generation,token_version FROM users WHERE id=:id"
                    ),
                    {"id": owner},
                )
            ).one()
            assert tuple(row) == (None, "retained-refresh", 3, 4)
        for statement in (
            "UPDATE users SET encrypted_access_token='orphan' WHERE id=:id",
            "UPDATE users SET google_access_token_expires_at=now() WHERE id=:id",
            "UPDATE users SET google_refresh_lease_owner='00000000-0000-0000-0000-000000000001' WHERE id=:id",
            "UPDATE users SET google_token_generation=-1 WHERE id=:id",
            "UPDATE users SET token_version=-1 WHERE id=:id",
            "UPDATE users SET encrypted_refresh_token=NULL,google_refresh_lease_owner='00000000-0000-0000-0000-000000000001',google_refresh_lease_until=now() WHERE id=:id",
        ):
            async with engine.begin() as db:
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(  # sarj-noqa: SARJ429 -- Deliberately submit contradictory state to prove the PostgreSQL invariant.
                            text(statement), {"id": owner}
                        )
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            )  # sarj-noqa: SARJ429 -- Remove only this test's disposable database.
        await admin.dispose()
