from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import override
import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.document_transform.application import TransformSubmission
from app.document_transform.qa_broker import (
    plan_qa_canary,
    read_submitted_qa_canary_task,
    submit_planned_qa_canary,
)
from app.document_transform.qa_grants import QaAuthority, QaSubmittedCanary
from app.document_transform.schemas import (
    DriveFileSource,
    OutputStrategy,
    QaDocumentTransformRequest,
    TransformMode,
)
from app.drive.schemas import (
    MIME_DOC,
    MIME_FOLDER,
    DriveNode,
    DrivePermission,
    DrivePermissionDetail,
    FilePermissionSnapshot,
)
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, GoogleUserId, TaskId, UserId
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId
from app.models.task import Task
from app.models.user import User
from app.tasks.queue import TaskEnqueuer
from app.tasks.service import PostgresTaskService


_SOURCE = FileId("qa-submit-source-folder")
_DESTINATION = FileId("qa-submit-destination-folder")
_DOCUMENT = FileId("qa-submit-server-selected-document")
_DRIVE = DriveId("qa-submit-drive")
_MODIFIED = "2026-09-07T12:00:00Z"


class _QaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIKI_POLISH29_QA_")
    database_url: str | None = None


def _qa_engine() -> AsyncEngine:
    dsn = _QaSettings().database_url
    if not dsn:
        pytest.skip("Requires an explicitly configured disposable PostgreSQL QA database")
    url = make_url(dsn)
    if (
        url.host not in {"127.0.0.1", "localhost", "::1"}
        or not (url.database or "").startswith("wiki_polish29_qa")
        or url.drivername != "postgresql+asyncpg"
    ):
        pytest.fail("Only a loopback PostgreSQL database named wiki_polish29_qa* is allowed")
    return create_async_engine(dsn, pool_size=12, max_overflow=0)


class _Drive:
    def __init__(self, owner_email: str) -> None:
        self._owner_email = owner_email

    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]:
        assert folder_id == _SOURCE
        assert max_pages > 0
        assert max_children > 0
        return [
            DriveNode(
                id=_DOCUMENT,
                name="Security Review",
                mime_type=MIME_DOC,
                parent_id=_SOURCE,
                modified_time=_MODIFIED,
            )
        ]

    async def read_file_permissions_bounded(
        self,
        file_id: FileId,
        *,
        max_pages: int,
        max_permissions: int,
    ) -> FilePermissionSnapshot:
        assert file_id == _DESTINATION
        assert max_pages > 0
        assert max_permissions > 0
        return FilePermissionSnapshot(
            file_id=file_id,
            mime_type=MIME_FOLDER,
            drive_id=None,
            parent_ids=(),
            version="17",
            owned_by_me=True,
            shared=False,
            can_add_children=True,
            owner_emails=(self._owner_email,),
            permissions=(
                DrivePermission(
                    id="owner",
                    principal_type="user",
                    role="owner",
                    email_address=self._owner_email,
                    permission_details=(
                        DrivePermissionDetail(
                            permission_type="file", role="owner", inherited=False
                        ),
                    ),
                ),
            ),
        )


class _Files:
    async def get(self, file_id: FileId) -> FileResponse:
        if file_id == _SOURCE:
            return FileResponse(
                id=file_id,
                name="Source",
                mime_type=MIME_FOLDER,
                parent_folder_id=None,
                drive_id=_DRIVE,
                modified_time=None,
            )
        if file_id == _DESTINATION:
            return FileResponse(
                id=file_id,
                name="Destination",
                mime_type=MIME_FOLDER,
                parent_folder_id=None,
                drive_id=_DRIVE,
                modified_time=None,
                can_add_children=True,
            )
        assert file_id == _DOCUMENT
        return FileResponse(
            id=file_id,
            name="Security Review",
            mime_type=MIME_DOC,
            parent_folder_id=_SOURCE,
            drive_id=_DRIVE,
            modified_time=_MODIFIED,
        )


class _Enqueuer(TaskEnqueuer):
    def __init__(self) -> None:
        self.task_ids: list[TaskId] = []

    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        raise NotImplementedError

    @override
    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        self.task_ids.append(task_id)


async def test_real_postgres_concurrent_qa_submit_creates_one_forced_copy_task() -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = UserId(uuid.uuid7())
    authority = QaAuthority(
        grant_id=DocumentTransformQaGrantId(uuid.uuid7()),
        user_id=owner,
        user_email=f"qa-{owner}@sarj.ai",
        execution_name="job/executions/one",
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        source_folder_id=_SOURCE,
        destination_folder_id=_DESTINATION,
        mode=TransformMode.BOTH,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        job_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    drive = _Drive(authority.user_email)
    files = _Files()
    plan = await plan_qa_canary(authority, drive=drive, files=files)
    queue = _Enqueuer()
    start = asyncio.Event()

    async def submit() -> TransformSubmission:
        await start.wait()
        async with sessions() as db:
            return await submit_planned_qa_canary(
                authority,
                plan_id=plan.plan_id,
                drive=drive,
                files=files,
                tasks=PostgresTaskService(db=db),
                enqueuer=queue,
            )

    try:
        async with sessions() as db:
            db.add(
                User(
                    id=owner,
                    email=authority.user_email,
                    google_id=GoogleUserId(str(owner)),
                )
            )
            await db.commit()
        pending = [asyncio.create_task(submit()) for _ in range(8)]
        start.set()
        submissions = await asyncio.gather(*pending)

        task_id = submissions[0].task.id
        assert {submission.task.id for submission in submissions} == {task_id}
        assert sum(not submission.replayed for submission in submissions) == 1
        assert queue.task_ids == [task_id] * len(submissions)
        async with sessions() as db:
            assert (
                await read_submitted_qa_canary_task(
                    authority,
                    submission=QaSubmittedCanary(plan_id=plan.plan_id, task_id=task_id),
                    tasks=PostgresTaskService(db=db),
                )
                == submissions[0].task
            )
            assert (
                await db.scalar(
                    select(func.count()).select_from(Task).where(Task.owner_user_id == owner)
                )
                == 1
            )
            task = await db.get(Task, task_id)
            assert task is not None
            payload = QaDocumentTransformRequest.model_validate(task.payload)
            assert payload.output_strategy is OutputStrategy.COPY
            assert payload.copy_destination_folder_id == _DESTINATION
            source = payload.source
            assert isinstance(source, DriveFileSource)
            assert source.file_id == _DOCUMENT
            assert source.expected_modified_time == _MODIFIED
    finally:
        async with sessions() as db:
            await db.execute(delete(User).where(User.id == owner))
            await db.commit()
        await engine.dispose()
