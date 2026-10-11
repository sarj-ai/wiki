from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
import uuid

import pytest
from sqlalchemy import delete, event, null, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.cache.service import CacheNamespace, CachePSqlService
from app.document_transform.schemas import TransformMode
from app.extras.schemas import PreferencesWrite
from app.extras.service import PostgresExtrasService, list_accessible_doc_icons
from app.identifiers import DriveId, FileId, GoogleUserId, IdempotencyKey, TaskId, UserId
from app.models.task import Task
from app.models.user import User
from app.models.wiki import DocIcon
from app.tasks.errors import TaskLeaseLostError, TaskNotFoundError
from app.tasks.schemas import TaskKind, TaskStatus
from app.tasks.service import PostgresTaskService
from tests.support import dependency
from tests.test_task_source_dedupe_postgres import _qa_engine
from tests.test_tasks import _transform_success_result


class _StorageDatabase(NamedTuple):
    sessions: async_sessionmaker[AsyncSession]
    owner: UserId
    other_owner: UserId
    icon_id: FileId


@pytest.fixture
async def storage_database() -> AsyncGenerator[_StorageDatabase]:
    validated = _qa_engine()
    engine = create_async_engine(validated.url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    await validated.dispose()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, other_owner = UserId(uuid.uuid7()), UserId(uuid.uuid7())
    icon_id = FileId(f"storage-perf-icon-{owner}")
    try:
        async with sessions() as db:
            for user_id in (owner, other_owner):
                db.add(
                    User(
                        id=user_id,
                        email=f"storage-perf-{user_id}@sarj.ai",
                        google_id=GoogleUserId(f"storage-perf-{user_id}"),
                        token_version=0,
                    )
                )
            await db.commit()
        yield _StorageDatabase(sessions, owner, other_owner, icon_id)
    finally:
        async with sessions() as db:
            await db.execute(delete(DocIcon).where(DocIcon.drive_id == icon_id))
            await db.execute(delete(User).where(User.id.in_((owner, other_owner))))
            await db.commit()
        await engine.dispose()


class _PermissionReader:
    def __init__(self, db: AsyncSession, database: _StorageDatabase) -> None:
        self.db = db
        self.database = database

    async def get_accessible_ids(self, file_ids: list[FileId]) -> set[FileId]:
        assert not self.db.in_transaction()
        # An independent cache checkout reproduces the provider permission path.
        await CachePSqlService(self.database.sessions).get(
            CacheNamespace.TEMPLATE_CATALOG, str(self.database.owner)
        )
        if self.database.icon_id not in file_ids:
            return set()
        return {self.database.icon_id}


async def test_document_icon_read_releases_connection_before_permission_cache_checkout(
    storage_database: _StorageDatabase,
) -> None:
    async with storage_database.sessions() as db:
        db.add(DocIcon(drive_id=storage_database.icon_id, icon="📚"))
        await db.commit()
    async with storage_database.sessions() as db:
        user = await db.get(User, storage_database.owner)
        assert user is not None
        icons = await asyncio.wait_for(
            list_accessible_doc_icons(
                PostgresExtrasService(db=db, user=user),
                dependency(_PermissionReader(db, storage_database)),
            ),
            timeout=2,
        )
        assert [icon.drive_id for icon in icons] == [storage_database.icon_id]
        assert not db.in_transaction()


async def _seed_task(
    database: _StorageDatabase, *, status: TaskStatus = TaskStatus.PENDING
) -> TaskId:
    task_id = TaskId(uuid.uuid7())
    now = datetime.now(UTC)
    async with database.sessions() as db:
        db.add(
            Task(
                id=task_id,
                owner_user_id=database.owner,
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                status=status,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                request_hash="0" * 64,
                payload={"private_payload": "private-content-" * 40000},
                checkpoint={"private_checkpoint": "private-checkpoint-" * 40000},
                attempt_count=0 if status is TaskStatus.PENDING else 1,
                created_at=now - timedelta(minutes=2),
                started_at=None if status is TaskStatus.PENDING else now - timedelta(minutes=1),
                finished_at=now if status is TaskStatus.SUCCEEDED else None,
                result=(
                    _transform_success_result(task_id, TransformMode.BOTH).model_dump(mode="json")
                    if status is TaskStatus.SUCCEEDED
                    else null()
                ),
                expires_at=now + timedelta(days=1),
            )
        )
        await db.commit()
    return task_id


class _Statements:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def record(
        self,
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        self.sql.append(statement)


async def test_public_task_poll_and_transitions_omit_large_private_columns(
    storage_database: _StorageDatabase,
) -> None:
    task_id = await _seed_task(storage_database)
    observed = _Statements()
    async with storage_database.sessions() as db:
        bind = db.bind
        assert bind is not None
        event.listen(bind.sync_engine, "before_cursor_execute", observed.record)
        try:
            service = PostgresTaskService(db=db)
            pending = await service.get_for_owner(task_id, storage_database.owner)
            assert pending.status is TaskStatus.PENDING
            poll_projection = observed.sql[-1].partition("FROM tasks")[0]
            assert "tasks.payload" not in poll_projection
            assert "tasks.checkpoint" not in poll_projection
            claim = await service.claim(task_id, lease_duration=timedelta(minutes=1))
            assert claim.task.status is TaskStatus.RUNNING
            returned = observed.sql[-1].partition(" RETURNING ")[2]
            assert "tasks.payload" not in returned
            assert "tasks.checkpoint" not in returned
            await service.record_resource(
                task_id, lease_token=claim.lease_token, resource_id=FileId("created-resource")
            )
            assert observed.sql[-1].partition(" RETURNING ")[2] == "tasks.id"
            with pytest.raises(TaskLeaseLostError):
                await service.record_resource(
                    task_id, lease_token=uuid.uuid7(), resource_id=FileId("unowned-resource")
                )
            failed = await service.fail(
                task_id,
                lease_token=claim.lease_token,
                error_code="provider_failed",
                error_detail=None,
            )
            assert failed.status is TaskStatus.FAILED
            assert failed.resource_id == "created-resource"
            returned = observed.sql[-1].partition(" RETURNING ")[2]
            assert "tasks.payload" not in returned
            assert "tasks.checkpoint" not in returned
        finally:
            event.remove(bind.sync_engine, "before_cursor_execute", observed.record)


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "bytes",
        "findings",
        "models",
        "malformed-findings",
        "malformed-models",
        "owner",
        "expired",
    ],
)
async def test_bounded_result_filters_before_materialization_in_one_locked_query(
    storage_database: _StorageDatabase, case: str
) -> None:
    task_id = await _seed_task(storage_database, status=TaskStatus.SUCCEEDED)
    async with storage_database.sessions() as db:
        result = (await db.execute(select(Task.result).where(Task.id == task_id))).scalar_one()
        assert isinstance(result, dict)
        values: dict[str, object] = {}
        if case == "bytes":
            result["tool_versions"] = {"large": "x" * 40000}
        elif case == "findings":
            result["findings"] = [{"oversized": True}] * 2
        elif case == "models":
            result["model_ids"] = ["model-a", "model-b"]
        elif case == "malformed-findings":
            result["findings"] = "invalid"
        elif case == "malformed-models":
            result["model_ids"] = {}
        elif case == "expired":
            values["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
        values["result"] = result
        await db.execute(update(Task).where(Task.id == task_id).values(**values))
        await db.commit()

    observed = _Statements()
    async with storage_database.sessions() as db:
        bind = db.bind
        assert bind is not None
        event.listen(bind.sync_engine, "before_cursor_execute", observed.record)
        try:
            owner = storage_database.other_owner if case == "owner" else storage_database.owner
            service = PostgresTaskService(db=db)
            if case == "valid":
                response = await service.get_for_owner_bounded_result(
                    task_id,
                    owner,
                    max_result_bytes=16000,
                    max_findings=1,
                    max_model_entries=1,
                    max_tool_entries=1,
                )
                assert response.status is TaskStatus.SUCCEEDED
                assert db.in_transaction()
            else:
                with pytest.raises(TaskNotFoundError):
                    await service.get_for_owner_bounded_result(
                        task_id,
                        owner,
                        max_result_bytes=16000,
                        max_findings=1,
                        max_model_entries=1,
                        max_tool_entries=1,
                    )
            assert len(observed.sql) == 1
            assert "FOR SHARE" in observed.sql[0]
            projection = observed.sql[0].partition("FROM tasks")[0]
            assert "tasks.payload" not in projection
            assert "tasks.checkpoint" not in projection
        finally:
            event.remove(bind.sync_engine, "before_cursor_execute", observed.record)


async def test_projected_preferences_write_preserves_unspecified_fields(
    storage_database: _StorageDatabase,
) -> None:
    async with storage_database.sessions() as db:
        user = await db.get(User, storage_database.owner)
        assert user is not None
        service = PostgresExtrasService(db=db, user=user)
        original = await service.get_preferences()
        assert original.show_non_doc_files is True
        assert original.hidden_workspace_ids == []
        await service.upsert_preferences(
            PreferencesWrite(hidden_workspace_ids=[DriveId("hidden-workspace")])
        )
        changed = await service.upsert_preferences(PreferencesWrite(show_non_doc_files=False))
        assert changed.show_non_doc_files is False
        assert changed.hidden_workspace_ids == ["hidden-workspace"]
        readback = await service.get_preferences()
        assert readback == changed
