from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, NamedTuple
import uuid

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid, create_engine, delete

from app.auth.dependencies import get_current_user
from app.files.dependencies import files_service_dep
from app.identifiers import IdempotencyKey
from app.main import app
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.errors import TaskNotFoundError
from app.tasks.schemas import TaskKind
from app.tasks.service import PostgresTaskService
from tests.test_files_service import _user


if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Connection, CursorResult, Select


class _Database:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.statements: list[Select[Any]] = []

    async def execute(self, statement: Select[Any]) -> CursorResult[Any]:
        self.statements.append(statement)
        return self.connection.execute(statement)


class _AdmissionStore(NamedTuple):
    service: PostgresTaskService
    database: _Database
    tasks: Table
    replay: Table


@pytest.fixture
def admission_store() -> Iterator[_AdmissionStore]:
    # Execute the actual projection/predicates against relational rows; this
    # does not claim PostgreSQL concurrency or migration coverage.
    metadata = MetaData()
    tasks = Table(
        "tasks",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("owner_user_id", Uuid),
        Column("kind", String),
        Column("status", String),
        Column("idempotency_key", Uuid),
        Column("expires_at", DateTime),
        Column("payload", String),
    )
    replay = Table(
        "task_replay_keys",
        metadata,
        Column("owner_user_id", Uuid),
        Column("kind", String),
        Column("idempotency_key", Uuid),
        Column("task_id", Uuid),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.connect() as connection:
        database = _Database(connection)
        db: Any = database
        yield _AdmissionStore(PostgresTaskService(db=db), database, tasks, replay)
    engine.dispose()


@pytest.mark.parametrize(
    "case",
    [
        "direct",
        "alias",
        "owner",
        "kind",
        "expired",
        "absent",
        "deleted",
        "alias_owner",
        "alias_kind",
        "alias_expired",
        "alias_deleted",
        "alias_task_owner",
        "alias_task_kind",
    ],
)
async def test_lookup_is_owner_kind_key_retention_scoped_and_read_only(
    admission_store: _AdmissionStore, case: str
) -> None:
    service, database, tasks, replay = admission_store
    owner, key, task_id = uuid.uuid7(), IdempotencyKey(uuid.uuid7()), uuid.uuid7()
    alias = case.startswith("alias")
    database.connection.execute(
        tasks.insert().values(
            id=task_id,
            owner_user_id=uuid.uuid7() if case in {"owner", "alias_task_owner"} else owner,
            kind=TaskKind.FILE_SMART_CREATE
            if case in {"kind", "alias_task_kind"}
            else TaskKind.FILE_DOCUMENT_TRANSFORM,
            status="pending",
            idempotency_key=uuid.uuid7() if alias or case == "absent" else key,
            expires_at=datetime.now(UTC)
            + timedelta(days=-1 if case in {"expired", "alias_expired"} else 1),
            payload="SECRET title source content result",
        )
    )
    if alias:
        database.connection.execute(
            replay.insert().values(
                owner_user_id=uuid.uuid7() if case == "alias_owner" else owner,
                kind=TaskKind.FILE_SMART_CREATE
                if case == "alias_kind"
                else TaskKind.FILE_DOCUMENT_TRANSFORM,
                idempotency_key=key,
                task_id=task_id,
            )
        )
    if case in {"deleted", "alias_deleted"}:
        database.connection.execute(delete(tasks).where(tasks.c.id == task_id))
    if case in {"direct", "alias"}:
        result = await service.get_document_transform_admission(owner, key)
        assert result.model_dump(mode="json") == {"task_id": str(task_id), "status": "pending"}
    else:
        with pytest.raises(TaskNotFoundError):
            await service.get_document_transform_admission(owner, key)
    assert len(database.statements) == 1
    assert list(database.statements[0].selected_columns.keys()) == ["id", "status"]
    assert database.statements[0].is_select


@pytest.mark.parametrize("case", ["found", "missing", "invalid", "unauthorized"])
async def test_admission_http_contract_has_no_private_data(
    admission_store: _AdmissionStore, case: str
) -> None:
    service, database, tasks, _replay = admission_store
    user = _user()
    key, task_id = uuid.uuid7(), uuid.uuid7()
    database.connection.execute(
        tasks.insert().values(
            id=task_id,
            owner_user_id=user.id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            status="failed",
            idempotency_key=key,
            expires_at=datetime.now(UTC) + timedelta(days=1),
            payload="SECRET",
        )
    )

    def unauthorized() -> None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_current_user] = (
        unauthorized if case == "unauthorized" else lambda: user
    )
    app.dependency_overrides[task_service_dep] = lambda: service
    value = "invalid" if case == "invalid" else str(uuid.uuid7() if case == "missing" else key)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/document-transforms/admissions/{value}")
    finally:
        app.dependency_overrides.clear()
    assert (
        response.status_code
        == {"found": 200, "missing": 404, "invalid": 422, "unauthorized": 401}[case]
    )
    assert "SECRET" not in response.text
    if case == "found":
        assert response.json() == {"task_id": str(task_id), "status": "failed"}
        assert response.headers["Cache-Control"] == "no-store"
    if case in {"invalid", "unauthorized"}:
        assert database.statements == []


async def test_public_transform_route_rejects_internal_qa_binding() -> None:
    user = _user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[task_service_dep] = object
    app.dependency_overrides[task_enqueuer_dep] = object
    app.dependency_overrides[files_service_dep] = object
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/document-transforms",
                headers={"Idempotency-Key": str(uuid.uuid7())},
                json={
                    "source": {
                        "kind": "drive_file",
                        "file_id": "source",
                        "expected_modified_time": "2026-09-03T00:00:00Z",
                    },
                    "mode": "unslop",
                    "output_strategy": "copy",
                    "copy_destination_folder_id": "destination",
                    "qa_destination_permission": {"version": "17", "sha256": "a" * 64},
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "qa_destination_permission" in response.text


def test_transform_openapi_explains_markdown_submission_and_recovery() -> None:
    schema = app.openapi()
    operation = schema["paths"]["/document-transforms"]["post"]
    assert "Markdown" in operation["summary"]
    assert "admissions/{idempotency_key}" in operation["description"]
    assert "Location" in operation["responses"]["202"]["headers"]
    markdown = schema["components"]["schemas"]["MarkdownSource"]["properties"]
    assert "UTF-8" in markdown["content"]["description"]
    assert "folder" in markdown["destination_folder_id"]["description"]
