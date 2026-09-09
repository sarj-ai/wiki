from __future__ import annotations

from typing import Any, Self
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.sql.elements import ClauseElement

from app.identifiers import TaskId
from app.tasks.errors import TaskEnqueueError
from app.tasks.internal_auth import (
    GoogleOidcTaskRequestAuthenticator,
    document_transform_task_request_authenticator_dep,
)
from app.tasks.internal_router import (
    reconcile_document_transform_dispatches,
    router as internal_task_router,
)
from app.tasks.service import PostgresTaskService


def test_reconciliation_requires_oidc_before_accessing_tasks() -> None:
    app = FastAPI()
    app.include_router(internal_task_router)
    app.dependency_overrides[document_transform_task_request_authenticator_dep] = lambda: (
        GoogleOidcTaskRequestAuthenticator(
            audience="https://wiki-api.sarj.ai/internal/tasks/document-transform",
            service_account_email="task-invoker@example.iam.gserviceaccount.com",
        )
    )
    with TestClient(app) as client:
        response = client.post("/internal/tasks/document-transform/reconcile")
    assert response.status_code == 401


class _Rows:
    def __init__(self, ids: list[TaskId]) -> None:
        self.ids = ids

    def scalars(self) -> Self:
        return self

    def all(self) -> list[TaskId]:
        return self.ids


class _Session:
    statement: ClauseElement | None = None

    async def execute(self, statement: ClauseElement) -> _Rows:
        self.statement = statement
        return _Rows([])


async def test_reconciliation_selects_only_validated_stale_unexpired_pending_intents() -> None:
    session = _Session()
    db: Any = session
    service = PostgresTaskService(db=db)
    assert await service.pending_document_transform_dispatches(limit=50) == []
    assert session.statement is not None
    sql = str(session.statement)
    assert "tasks.dispatch_ready_at <=" in sql
    assert "tasks.expires_at > now()" in sql
    assert "tasks.status =" in sql
    assert "tasks.kind =" in sql
    params = session.statement.compile().params or {}
    assert "pending" in params.values()
    assert "file.document_transform" in params.values()
    assert 50 in params.values()


@pytest.mark.parametrize("limit", [0, 101])
async def test_reconciliation_batch_is_bounded(limit: int) -> None:
    db: Any = _Session()
    with pytest.raises(ValueError, match="between 1 and 100"):
        await PostgresTaskService(db=db).pending_document_transform_dispatches(limit=limit)


class _Intents:
    def __init__(self, ids: list[TaskId]) -> None:
        self.ids = ids

    async def pending_document_transform_dispatches(self, *, limit: int) -> list[TaskId]:
        assert limit == 50
        return self.ids


class _Queue:
    def __init__(self) -> None:
        self.created: set[TaskId] = set()
        self.fail = True

    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        if self.fail:
            raise TaskEnqueueError
        self.created.add(task_id)


async def test_interrupted_enqueue_recovers_same_identity_and_replays_converge() -> None:
    ids = [TaskId(uuid.uuid7()), TaskId(uuid.uuid7())]
    tasks: Any = _Intents(ids)
    queue = _Queue()
    enqueuer: Any = queue
    failed = await reconcile_document_transform_dispatches(tasks, enqueuer)
    assert failed == {"selected": 2, "enqueued": 0, "failed": 2}
    assert queue.created == set()
    queue.fail = False
    recovered = await reconcile_document_transform_dispatches(tasks, enqueuer)
    assert recovered == {"selected": 2, "enqueued": 2, "failed": 0}
    await reconcile_document_transform_dispatches(tasks, enqueuer)
    assert queue.created == set(ids)
