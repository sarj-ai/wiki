import asyncio
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.document_transform.errors import OutputConflictError
from app.identifiers import GoogleUserId, UserId
from app.models.task import Task
from app.models.user import User
from app.models.wiki import DocumentHeaderProvenance
from app.tasks.errors import TaskLeaseLostError
from app.tasks.schemas import TaskStatus
from app.tasks.service import PostgresTaskService
from tests.test_header_provenance_transaction import prepared_provenance
from tests.test_task_source_dedupe_postgres import _qa_engine


@pytest.mark.parametrize(
    "scenario", ["success", "identical", "conflict", "lost_lease", "concurrent"]
)
async def test_real_postgres_header_provenance_atomicity_and_retention(scenario: str) -> None:
    engine = _qa_engine()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    task, result, expected = await prepared_provenance(
        "conflict" if scenario == "conflict" else "success"
    )
    lease = uuid.uuid7()
    task.status = TaskStatus.RUNNING
    del task.result
    task.finished_at = None
    task.request_hash = "d" * 64
    task.lease_token = lease
    assert task.started_at is not None
    task.lease_expires_at = task.started_at + timedelta(minutes=5)

    async def complete():
        async with sessions() as session:
            return await PostgresTaskService(db=session).succeed(
                task.id,
                lease_token=uuid.uuid7() if scenario == "lost_lease" else lease,
                result=result,
            )

    try:
        async with sessions() as session:
            session.add(
                User(
                    id=UserId(task.owner_user_id),
                    email=f"qa-{task.owner_user_id}@sarj.ai",
                    google_id=GoogleUserId(str(task.owner_user_id)),
                )
            )
            await session.flush()
            session.add(task)
            if scenario in {"identical", "conflict"}:
                session.add(expected)
            await session.commit()
        if scenario == "concurrent":
            attempts = await asyncio.gather(complete(), complete(), return_exceptions=True)
            assert sum(isinstance(item, TaskLeaseLostError) for item in attempts) == 1
            assert sum(not isinstance(item, BaseException) for item in attempts) == 1
        elif scenario in {"conflict", "lost_lease"}:
            with pytest.raises(
                OutputConflictError if scenario == "conflict" else TaskLeaseLostError
            ):
                await complete()
        else:
            await complete()
        async with sessions() as session:
            stored_task = await session.get(Task, task.id)
            assert stored_task is not None
            stored = await session.get(
                DocumentHeaderProvenance, (task.owner_user_id, result.output.file_id)
            )
            match scenario:
                case "lost_lease":
                    assert stored is None
                    assert stored_task.status == TaskStatus.RUNNING
                case "conflict":
                    assert stored is not None
                    assert stored.result_sha256 == "c" * 64
                    assert stored_task.status == TaskStatus.RUNNING
                    assert stored_task.result is None
                case _:
                    assert stored is not None
                    assert stored.result_sha256 == expected.result_sha256
                    assert stored_task.status == TaskStatus.SUCCEEDED
                    assert (
                        await session.scalar(
                            select(DocumentHeaderProvenance).where(
                                DocumentHeaderProvenance.owner_user_id == uuid.uuid7(),
                                DocumentHeaderProvenance.file_id == result.output.file_id,
                            )
                        )
                        is None
                    )
                    await session.execute(delete(Task).where(Task.id == task.id))
                    await session.commit()
                    session.expire_all()
                    assert (
                        await session.get(
                            DocumentHeaderProvenance, (task.owner_user_id, result.output.file_id)
                        )
                        is not None
                    )
    finally:
        await engine.dispose()
