from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import (
    ARRAY,
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    MarkdownSource,
    PlannedPresentationCheckpoint,
    TransformMode,
)
from app.extras.schemas import FolderIconWrite, PreferencesWrite, SortOrderWrite
from app.extras.service import PostgresExtrasService
from app.identifiers import DriveId, FileId, GoogleUserId, IdempotencyKey, UserId
from app.mcp.native_admission import PostgresRegistrationAdmission, RegistrationAdmissionDeniedError
from app.models.mcp_oauth import MCPOAuthEntry
from app.models.task import Task
from app.models.user import User
from app.models.wiki import CacheEntry
from app.tasks.errors import TaskNotClaimableError
from app.tasks.schemas import NeedsReviewTaskRead, ReviewDocumentTransformTaskRecord, TaskKind
from app.tasks.service import PostgresTaskService
from tests.test_task_source_dedupe_postgres import _qa_engine


_BACKEND = Path(__file__).parents[1]


def _upgrade(dsn: str, revision: str) -> None:
    _alembic(dsn, "upgrade", revision)


def _alembic(dsn: str, *arguments: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND,
        env=os.environ | {"DATABASE_URL": dsn},  # ruff: ignore[banned-api] — preserve the subprocess toolchain environment.
        check=True,
        capture_output=True,
        text=True,
    )


async def test_upgrade_preserves_settings_tokens_and_concurrent_user_edits() -> None:
    owner = uuid.uuid7()
    other = uuid.uuid7()
    async with _migration_database() as engine:
        dsn = engine.url.render_as_string(hide_password=False)
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


async def test_google_token_invariants_preserve_refresh_and_reject_contradictions() -> None:
    owner = uuid.uuid7()
    async with _migration_database() as engine:
        dsn = engine.url.render_as_string(hide_password=False)
        await asyncio.to_thread(_upgrade, dsn, "0026_retire_oauth_proxy")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as db:
            db.add(
                User(
                    id=owner,
                    email="credential@example.com",
                    google_id="credential",
                    encrypted_access_token="unusable-access",
                    encrypted_refresh_token="retained-refresh",
                    google_token_generation=3,
                    token_version=4,
                )
            )
        await asyncio.to_thread(_upgrade, dsn, "head")
        async with sessions() as db:
            user = await db.get(User, owner)
            assert user is not None
            assert user.encrypted_access_token is None
            assert user.encrypted_refresh_token == "retained-refresh"
            assert user.google_token_generation == 3
            assert user.token_version == 4
        lease_owner = uuid.uuid7()
        for statement in (
            update(User).values(encrypted_access_token="orphan"),
            update(User).values(google_access_token_expires_at=datetime.now(UTC)),
            update(User).values(google_refresh_lease_owner=lease_owner),
            update(User).values(google_token_generation=-1),
            update(User).values(token_version=-1),
            update(User).values(
                encrypted_refresh_token=None,
                google_refresh_lease_owner=lease_owner,
                google_refresh_lease_until=datetime.now(UTC),
            ),
        ):
            async with sessions.begin() as db:
                with pytest.raises(IntegrityError):
                    async with db.begin_nested():
                        await db.execute(statement.where(User.id == owner))


@asynccontextmanager
async def _migration_database() -> AsyncGenerator[AsyncEngine]:
    admin = _qa_engine()
    name = f"wiki_polish29_qa_storage_{uuid.uuid7().hex}"
    engine = create_async_engine(admin.url.set(database=name))
    try:
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f'CREATE DATABASE "{name}"')
            )  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
        yield engine
    finally:
        await engine.dispose()
        async with admin.connect() as connection:
            await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            )  # sarj-noqa: SARJ429 -- Exercise legacy schema migration and database invariants before the current ORM applies.
        await admin.dispose()


async def test_review_migration_preserves_expired_execution_and_refuses_lossy_downgrade() -> None:
    async with _migration_database() as engine:
        dsn = engine.url.render_as_string(hide_password=False)
        await asyncio.to_thread(_upgrade, dsn, "0028_task_result_invariants")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = UserId(uuid.uuid7())
        key = IdempotencyKey(uuid.uuid7())
        payload = DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content="Original evidence\n",
                name="Recovery evidence",
                destination_folder_id=FileId("qa-folder"),
            ),
            mode=TransformMode.FORMAT,
        )
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            service = PostgresTaskService(db=session)
            reservation = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=key,
                payload=payload,
            )
            task_id = reservation.task.id
            await service.claim(task_id, lease_duration=timedelta(minutes=1))
        await engine.dispose()
        with pytest.raises(subprocess.CalledProcessError) as active:
            await asyncio.to_thread(_upgrade, dsn, "0029_task_review")
        assert "Drain running executions before migrating task review state" in active.value.stderr
        async with sessions() as session:
            row = await session.get(Task, task_id)
            assert row is not None
            evidence = dict(row.payload)
            now = datetime.now(UTC)
            row.created_at = now - timedelta(days=2)
            row.started_at = now - timedelta(days=1)
            row.lease_expires_at = now - timedelta(minutes=2)
            row.expires_at = now - timedelta(minutes=1)
            await session.commit()
        await engine.dispose()
        await asyncio.to_thread(_upgrade, dsn, "0029_task_review")
        async with sessions() as session:
            service = PostgresTaskService(db=session)
            retained = await service.get_for_owner(task_id, owner)
            assert isinstance(retained, NeedsReviewTaskRead)
            record = await service.load_document_transform(task_id)
            assert isinstance(record, ReviewDocumentTransformTaskRecord)
            row = await session.get(Task, task_id)
            assert row is not None
            assert row.payload == evidence
            assert row.checkpoint is None
            assert row.checkpoint_generation == 0
            with pytest.raises(TaskNotClaimableError):
                await service.claim(task_id, lease_duration=timedelta(minutes=1))
            replay = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=key,
                payload=payload,
            )
            assert not replay.created
            assert replay.task.id == task_id
            assert isinstance(replay.task, NeedsReviewTaskRead)
        await engine.dispose()
        with pytest.raises(subprocess.CalledProcessError) as rejected:
            await asyncio.to_thread(_alembic, dsn, "downgrade", "0028_task_result_invariants")
        assert "Resolve review records explicitly before downgrading" in rejected.value.stderr
        async with sessions() as session:
            row = await session.get(Task, task_id)
            assert row is not None
            assert row.payload == evidence


@pytest.mark.parametrize("blocker", ["running", "presentation_checkpoint"])
async def test_downgrade_refuses_active_work_and_new_checkpoint_formats(blocker: str) -> None:
    async with _migration_database() as engine:
        dsn = engine.url.render_as_string(hide_password=False)
        await asyncio.to_thread(_upgrade, dsn, "0029_task_review")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner = UserId(uuid.uuid7())
        async with sessions() as session:
            session.add(
                User(id=owner, email=f"qa-{owner}@sarj.ai", google_id=GoogleUserId(str(owner)))
            )
            await session.commit()
            service = PostgresTaskService(db=session)
            reservation = await service.create_or_replay(
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                owner_user_id=owner,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=DocumentTransformRequest(
                    source=DriveFileSource(
                        kind="drive_file",
                        file_id=FileId("qa-slide-source"),
                        expected_modified_time="2026-09-05T00:00:00Z",
                    ),
                    mode=TransformMode.FORMAT,
                ),
            )
            task_id = reservation.task.id
            claim = await service.claim(task_id, lease_duration=timedelta(minutes=1))
            if blocker == "presentation_checkpoint":
                await service.save_document_transform_checkpoint(
                    task_id,
                    lease_token=claim.lease_token,
                    expected_generation=0,
                    checkpoint=PlannedPresentationCheckpoint(
                        request_sha256="a" * 64, source_sha256="b" * 64, output_name="QA output"
                    ),
                )
                await service.fail(
                    task_id,
                    lease_token=claim.lease_token,
                    error_code="cancelled_before_dispatch",
                    error_detail="No provider request was sent",
                )
        await engine.dispose()
        with pytest.raises(subprocess.CalledProcessError) as refused:
            await asyncio.to_thread(_alembic, dsn, "downgrade", "0028_task_result_invariants")
        expected = (
            "Drain running executions before downgrading"
            if blocker == "running"
            else "Retire presentation checkpoints before downgrading"
        )
        assert expected in refused.value.stderr
        async with sessions() as session:
            row = await session.get(Task, task_id)
            assert row is not None
            assert row.status == ("running" if blocker == "running" else "failed")
