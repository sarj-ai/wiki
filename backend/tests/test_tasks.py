from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from typing import Any
import uuid

from pydantic import BaseModel
import pytest
from sqlalchemy import Table
from sqlalchemy.sql.elements import ClauseElement

from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
    QaCopyPermissionEvidence,
    QaDestinationPermissionBinding,
    QaDocumentTransformRequest,
    QaFilePermissionEvidence,
    TransformMode,
    ValidationSummary,
)
from app.document_transform.versions import DOCUMENT_PIPELINE_VERSION
from app.files.schemas import FileResponse
from app.identifiers import DocsRevisionId, FileId, IdempotencyKey, TaskId, UserId
from app.models.task import Task
from app.smart_create.schemas import SmartCreateResult
from app.tasks import service as task_service_module
from app.tasks.errors import (
    TaskAdmissionLimitError,
    TaskIdempotencyConflictError,
    TaskLeaseLostError,
    TaskNotFoundError,
    TaskTargetBusyError,
)
from app.tasks.schemas import TaskKind, TaskRead, TaskStatus
from app.tasks.service import PostgresTaskService


class _Payload(BaseModel):
    name: str
    destination: str


class _Result:
    def __init__(self, row: object) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row

    def scalar_one(self) -> Any:
        return self._row

    def one_or_none(self) -> Any:
        if isinstance(self._row, Task):
            return (self._row, False)
        return self._row

    def one(self) -> Any:
        return self._row


class _Session:
    def __init__(self, *rows: object) -> None:
        self._rows = iter(rows)
        self.commits = 0
        self.rollbacks = 0
        self.statements: list[object] = []
        self.new: set[object] = set()
        self.dirty: set[object] = set()
        self.deleted: set[object] = set()

    async def execute(self, statement: object) -> _Result:
        self.statements.append(statement)
        return _Result(next(self._rows))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _task(*, request_hash: str, status: TaskStatus = TaskStatus.PENDING) -> Task:
    now = datetime.now(UTC)
    task = Task()
    task.id = TaskId(uuid.uuid7())
    task.owner_user_id = uuid.uuid7()
    task.kind = TaskKind.FILE_SMART_CREATE
    task.status = status
    task.idempotency_key = IdempotencyKey(uuid.uuid7())
    task.request_hash = request_hash
    task_payload: dict[str, object] = {"destination": "folder-1", "name": "PRD"}
    task.payload = task_payload
    task.result = None
    task.checkpoint = None
    task.checkpoint_generation = 0
    task.resource_id = None
    task.in_place_target = None
    task.error_code = None
    task.error_detail = None
    task.attempt_count = 0
    task.lease_token = None
    task.lease_expires_at = None
    task.created_at = now
    task.started_at = None
    task.finished_at = None
    task.updated_at = now
    task.expires_at = now + timedelta(days=7)
    return task


def _service(session: _Session) -> PostgresTaskService:
    db: Any = session
    return PostgresTaskService(db=db)


def _in_place_payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("document-1"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=TransformMode.UNSLOP,
        output_strategy=OutputStrategy.IN_PLACE,
    )


def _markdown_payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# Private decision\n\nThe board approved option B.",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.BOTH,
    )


def _qa_copy_payload() -> QaDocumentTransformRequest:
    return QaDocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("document-1"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=TransformMode.BOTH,
        output_strategy=OutputStrategy.COPY,
        copy_destination_folder_id=FileId("owner-only-folder"),
        qa_destination_permission=QaDestinationPermissionBinding(version="17", sha256="a" * 64),
    )


def test_internal_qa_payload_round_trips_through_worker_storage_decoder() -> None:
    payload = _qa_copy_payload()

    serialized = task_service_module._serialize(payload)
    restored = task_service_module._deserialize_document_transform_payload(serialized)

    assert serialized["qa_contract"] == "owner_only_v1"
    assert serialized["qa_destination_permission"] == {
        "version": "17",
        "sha256": "a" * 64,
    }
    assert isinstance(restored, QaDocumentTransformRequest)
    assert restored == payload


def test_public_task_read_strips_internal_qa_permission_evidence() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.SUCCEEDED)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    row.attempt_count = 1
    row.started_at = row.created_at
    row.finished_at = row.created_at
    result = _transform_success_result(row.id, TransformMode.BOTH)
    internal_result: dict[str, object] = result.model_dump(mode="json")
    internal_result["access_scope"] = "owner_only"
    internal_result["qa_permission_evidence"] = {"private": "must-not-leak"}
    row.result = internal_result

    public = task_service_module._to_read(row)

    assert public.result is not None
    serialized = public.model_dump(mode="json")
    assert "qa_permission_evidence" not in json.dumps(serialized)


async def test_qa_permission_evidence_read_is_exact_owner_task_and_size_scoped() -> None:
    task_id = TaskId(uuid.uuid7())
    owner_id = UserId(uuid.uuid7())
    observed_at = datetime.now(UTC)
    evidence = QaCopyPermissionEvidence(
        operation_id=task_id,
        source_sha256="a" * 64,
        output_revision_id=DocsRevisionId("revision-2"),
        destination=QaFilePermissionEvidence(
            file_id=FileId("destination"),
            mime_type="application/vnd.google-apps.folder",
            parent_folder_id=None,
            version="17",
            permission_sha256="b" * 64,
            observed_at=observed_at,
        ),
        output=QaFilePermissionEvidence(
            file_id=FileId("output"),
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("destination"),
            version="19",
            permission_sha256="c" * 64,
            observed_at=observed_at,
        ),
    )
    session = _Session(SimpleNamespace(evidence=evidence.model_dump(mode="json")))

    restored = await _service(session).get_qa_permission_evidence(
        task_id, owner_id, max_evidence_bytes=16 * 1024
    )

    assert restored == evidence
    sql = str(session.statements[0])
    assert "tasks.id" in sql
    assert "tasks.owner_user_id" in sql
    assert "tasks.kind" in sql
    assert "tasks.status" in sql
    assert "tasks.expires_at > now()" in sql
    assert "jsonb_typeof" in sql
    assert "octet_length" in sql


def _success_result(task_id: TaskId) -> SmartCreateResult:
    return SmartCreateResult(
        task_id=task_id,
        file=FileResponse(
            id=FileId("created"),
            name="Decision",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("folder"),
            drive_id=None,
            modified_time=None,
        ),
        warnings=[],
    )


def _transform_success_result(task_id: TaskId, mode: TransformMode) -> DocumentTransformResult:
    return DocumentTransformResult(
        operation_id=task_id,
        source_sha256="0" * 64,
        output=DocumentOutput(
            file_id=FileId("output"),
            name="Decision",
            parent_folder_id=FileId("folder"),
        ),
        mode=mode,
        applied_patch_count=0,
        findings=[],
        validation=ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=True,
            structure_preserved=True,
            unsafe_patch_count=0,
        ),
        model_ids=[],
        tool_versions={},
        pipeline_version=DOCUMENT_PIPELINE_VERSION,
        needs_review=False,
    )


async def test_in_place_admission_reserves_target_before_queueing() -> None:
    row = _task(request_hash="0" * 64)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, None, None, (0, 0), row)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=_in_place_payload(),
    )

    assert reservation.created is True
    statements = "\n".join(str(statement) for statement in session.statements)
    assert "pg_try_advisory_xact_lock" in statements
    assert "in_place_target" in statements
    assert session.commits == 1


async def test_admission_finishes_before_dispatch_intent_is_committed() -> None:
    row = _task(request_hash="0" * 64)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, None, None, (0, 0), row)

    async def admit() -> None:
        assert session.commits == 0
        assert not any("INSERT INTO tasks" in str(query) for query in session.statements)

    await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=_in_place_payload(),
        admit=admit,
    )
    statement = session.statements[-1]
    assert isinstance(statement, ClauseElement)
    assert (statement.compile().params or {})["dispatch_ready_at"] is not None
    assert session.commits == 1


async def test_failed_admission_never_publishes_or_discards_a_reservation() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(True, None, None, 0, 0)

    async def admit() -> None:
        message = "Admission denied"
        raise PermissionError(message)

    with pytest.raises(PermissionError, match="Admission denied"):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=row.idempotency_key,
            payload=_in_place_payload(),
            admit=admit,
        )
    assert session.commits == 0
    assert session.rollbacks == 1
    assert not any("INSERT INTO tasks" in str(query) for query in session.statements)


async def test_admitted_replay_does_not_revalidate_changed_provider_state() -> None:
    payload = _in_place_payload()
    row = _task(
        request_hash=task_service_module._request_hash(task_service_module._serialize(payload))
    )
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, row)

    async def admit() -> None:
        pytest.fail("An already admitted replay must not inspect changed provider state")

    result = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=payload,
        admit=admit,
    )
    assert result.created is False


async def test_distinct_active_in_place_task_for_same_target_is_rejected() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(True, None, None, None, (0, 0), None, None, None)

    with pytest.raises(TaskTargetBusyError):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=row.idempotency_key,
            payload=_in_place_payload(),
        )

    assert session.rollbacks == 1


async def test_document_transform_admission_caps_all_active_owner_tasks() -> None:
    row = _task(request_hash="0" * 64)
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("document-1"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=TransformMode.UNSLOP,
    )
    session = _Session(True, None, None, None, (20, 0))

    with pytest.raises(TaskAdmissionLimitError):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=row.idempotency_key,
            payload=payload,
        )

    assert session.rollbacks == 1


@pytest.mark.parametrize("counts", [(None, 0), (0, None), ("20", 0), (0, 10.5)])
async def test_invalid_database_counts_fail_closed_before_task_admission(
    counts: tuple[object, object],
) -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(True, None, None, None, counts)

    with pytest.raises(TypeError, match="Active task count query returned an invalid value"):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=row.idempotency_key,
            payload=_in_place_payload(),
        )

    assert session.commits == 0
    assert not any("INSERT INTO tasks" in str(statement) for statement in session.statements)


async def test_document_transform_admission_replay_precedes_owner_cap() -> None:
    payload = _in_place_payload()
    serialized = task_service_module._serialize(payload)
    row = _task(request_hash=task_service_module._request_hash(serialized))
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, row)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=payload,
    )

    assert reservation.created is False
    assert reservation.task.id == row.id
    assert len(session.statements) == 2


@pytest.mark.parametrize("strategy", [OutputStrategy.COPY, OutputStrategy.IN_PLACE])
@pytest.mark.parametrize("task_status", [TaskStatus.PENDING, TaskStatus.RUNNING])
async def test_different_key_same_source_reuses_active_task(
    strategy: OutputStrategy, task_status: TaskStatus
) -> None:
    payload = _in_place_payload().model_copy(update={"output_strategy": strategy})
    row = _task(
        request_hash=task_service_module._request_hash(task_service_module._serialize(payload)),
        status=task_status,
    )
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, row, row, None)
    if task_status is TaskStatus.RUNNING:
        row.started_at = datetime.now(UTC)
        row.attempt_count = 1
    admitted = False

    async def admit() -> None:
        nonlocal admitted
        admitted = True
        assert session.commits == 0
        assert "pg_try_advisory_xact_lock" in str(session.statements[0])

    def admit_new() -> None:
        pytest.fail("Active replay must not run new-task freshness admission")

    new_key = IdempotencyKey(uuid.uuid7())
    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=new_key,
        payload=payload,
        admit=admit,
        admit_new=admit_new,
    )
    assert admitted
    assert not reservation.created
    assert reservation.task.id == row.id
    assert session.commits == 1
    query = session.statements[2]
    assert isinstance(query, ClauseElement)
    params = query.compile().params or {}
    assert row.owner_user_id in params.values()
    assert "document-1" in params.values()
    assert "tasks.expires_at > now()" in str(query)
    assert "tasks.lease_expires_at > now()" in str(query)
    assert "FOR UPDATE" in str(session.statements[3])
    assert "clock_timestamp()" in str(session.statements[3])
    assert "INSERT INTO task_replay_keys" in str(session.statements[4])
    assert not any("INSERT INTO tasks " in str(statement) for statement in session.statements)


@pytest.mark.parametrize("change", ["mode", "source_revision", "strategy", "name"])
async def test_active_source_different_intent_is_busy(change: str) -> None:
    payload = _in_place_payload()
    row = _task(
        request_hash=task_service_module._request_hash(task_service_module._serialize(payload))
    )
    match change:
        case "mode":
            changed = payload.model_copy(update={"mode": TransformMode.FORMAT})
        case "source_revision":
            changed = payload.model_copy(
                update={
                    "source": payload.source.model_copy(update={"expected_modified_time": "new"})
                }
            )
        case "strategy":
            changed = payload.model_copy(update={"output_strategy": OutputStrategy.COPY})
        case _:
            changed = payload.model_copy(update={"output_name": "Different name"})
    session = _Session(True, None, row, row)
    with pytest.raises(TaskTargetBusyError):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=changed,
        )
    assert session.rollbacks == 1
    assert session.commits == 0


async def test_active_source_reuse_rechecks_revoked_access() -> None:
    payload = _in_place_payload()
    row = _task(
        request_hash=task_service_module._request_hash(task_service_module._serialize(payload))
    )
    session = _Session(True, None, row)

    async def admit() -> None:
        message = "Source access revoked"
        raise PermissionError(message)

    with pytest.raises(PermissionError, match="revoked"):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=payload,
            admit=admit,
        )
    assert session.rollbacks == 1
    assert len(session.statements) == 3


async def test_secondary_key_replay_query_retains_owner_and_terminal_history() -> None:
    payload = _in_place_payload()
    row = _task(
        request_hash=task_service_module._request_hash(task_service_module._serialize(payload)),
        status=TaskStatus.CANCELLED,
    )
    row.finished_at = datetime.now(UTC)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, row)
    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=IdempotencyKey(uuid.uuid7()),
        payload=payload,
    )
    assert reservation.task.id == row.id
    assert not reservation.created
    sql = str(session.statements[1])
    assert "task_replay_keys.owner_user_id" in sql
    assert "task_replay_keys.idempotency_key" in sql
    assert "task_replay_keys.kind" in sql
    assert "tasks.expires_at >" in sql


async def test_retained_verified_terminal_transform_replays_after_fresh_admission() -> None:
    payload = _in_place_payload()
    row = _task(
        request_hash=task_service_module._request_hash(task_service_module._serialize(payload)),
        status=TaskStatus.SUCCEEDED,
    )
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    row.started_at = datetime.now(UTC)
    row.finished_at = datetime.now(UTC)
    row.attempt_count = 1
    row.result = _transform_success_result(row.id, payload.mode).model_dump(mode="json")
    session = _Session(True, None, None, row, row, None)
    admissions: list[bool] = []
    retained_output_checks: list[TaskId] = []

    async def admit() -> None:
        admissions.append(True)

    def admit_new() -> None:
        pytest.fail("Terminal replay must not run new-task freshness admission")

    async def admit_terminal_replay(task: TaskRead) -> bool:
        retained_output_checks.append(task.id)
        return True

    fresh_key = IdempotencyKey(uuid.uuid7())
    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=fresh_key,
        payload=payload,
        admit=admit,
        admit_new=admit_new,
        admit_terminal_replay=admit_terminal_replay,
    )

    assert admissions == [True]
    assert retained_output_checks == [row.id]
    assert not reservation.created
    assert reservation.task.id == row.id
    terminal_statement = session.statements[3]
    assert isinstance(terminal_statement, ClauseElement)
    terminal_sql = str(terminal_statement)
    assert "tasks.status" in terminal_sql
    assert "tasks.finished_at IS NOT NULL" in terminal_sql
    terminal_params = terminal_statement.compile().params or {}
    assert "pipeline_version" in terminal_params.values()
    assert "brand_profile_sha256" in terminal_params.values()
    locked_terminal_statement = session.statements[4]
    assert isinstance(locked_terminal_statement, ClauseElement)
    assert "FOR UPDATE" in str(locked_terminal_statement)
    assert "INSERT INTO task_replay_keys" in str(session.statements[5])
    assert not any("INSERT INTO tasks " in str(statement) for statement in session.statements)


async def test_current_in_place_result_is_exact_revision_mode_pipeline_and_brand_match() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.SUCCEEDED)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    row.in_place_target = FileId("document-1")
    row.started_at = row.created_at
    row.finished_at = row.created_at
    row.attempt_count = 1
    result = _transform_success_result(row.id, TransformMode.FORMAT).model_copy(
        update={
            "output": DocumentOutput(
                file_id=FileId("document-1"),
                name="Decision",
                parent_folder_id=FileId("folder"),
            ),
            "output_strategy": OutputStrategy.IN_PLACE,
            "output_revision_id": DocsRevisionId("revision-current"),
        }
    )
    row.result = result.model_dump(mode="json")
    session = _Session(row)

    current = await _service(session).get_current_in_place_result(
        UserId(row.owner_user_id),
        FileId("document-1"),
        TransformMode.FORMAT,
        DocsRevisionId("revision-current"),
    )

    assert current == result
    statement = session.statements[0]
    assert isinstance(statement, ClauseElement)
    sql = str(statement)
    assert "tasks.in_place_target" in sql
    params = list((statement.compile().params or {}).values())
    assert "pipeline_version" in params
    assert "brand_profile_sha256" in params
    assert "document-1" in params
    assert "format" in params
    assert DOCUMENT_PIPELINE_VERSION in params
    assert "revision-current" not in params


async def test_current_in_place_result_returns_none_without_exact_match() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.SUCCEEDED)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    row.in_place_target = FileId("document-1")
    row.started_at = row.created_at
    row.finished_at = row.created_at
    row.attempt_count = 1
    row.result = (
        _transform_success_result(row.id, TransformMode.BOTH)
        .model_copy(
            update={
                "output": DocumentOutput(
                    file_id=FileId("document-1"),
                    name="Decision",
                    parent_folder_id=FileId("folder"),
                ),
                "output_strategy": OutputStrategy.IN_PLACE,
                "output_revision_id": DocsRevisionId("revision-before-user-edit"),
            }
        )
        .model_dump(mode="json")
    )

    assert (
        await _service(_Session(row)).get_current_in_place_result(
            UserId(row.owner_user_id),
            FileId("document-1"),
            TransformMode.BOTH,
            DocsRevisionId("user-edited-revision"),
        )
        is None
    )


@pytest.mark.parametrize("changed_field", ["expires_at", "status"])
async def test_terminal_changed_during_output_admission_creates_replacement(
    changed_field: str,
) -> None:
    payload = _markdown_payload()
    request_hash = task_service_module._request_hash(task_service_module._serialize(payload))
    terminal = _task(request_hash=request_hash, status=TaskStatus.SUCCEEDED)
    terminal.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    terminal.started_at = datetime.now(UTC)
    terminal.finished_at = datetime.now(UTC)
    terminal.attempt_count = 1
    terminal.result = _transform_success_result(terminal.id, payload.mode).model_dump(mode="json")
    replacement = _task(request_hash=request_hash)
    replacement.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, None, terminal, None, (0, 0), replacement)
    new_admissions: list[bool] = []

    async def admit_terminal_replay(_task: TaskRead) -> bool:
        if changed_field == "expires_at":
            terminal.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            terminal.status = TaskStatus.CANCELLED
        return True

    def admit_new() -> None:
        new_admissions.append(True)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=terminal.owner_user_id,
        idempotency_key=IdempotencyKey(uuid.uuid7()),
        payload=payload,
        admit_new=admit_new,
        admit_terminal_replay=admit_terminal_replay,
    )

    assert reservation.created
    assert reservation.task.id == replacement.id
    assert new_admissions == [True]
    locked_terminal_statement = session.statements[4]
    assert isinstance(locked_terminal_statement, ClauseElement)
    locked_sql = str(locked_terminal_statement)
    assert "FOR UPDATE" in locked_sql
    assert "tasks.status" in locked_sql
    assert "tasks.expires_at >" in locked_sql
    assert not any(
        "INSERT INTO task_replay_keys" in str(statement) for statement in session.statements
    )


async def test_missing_terminal_output_creates_exactly_one_replacement() -> None:
    payload = _markdown_payload()
    request_hash = task_service_module._request_hash(task_service_module._serialize(payload))
    terminal = _task(request_hash=request_hash, status=TaskStatus.SUCCEEDED)
    terminal.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    terminal.started_at = datetime.now(UTC)
    terminal.finished_at = datetime.now(UTC)
    terminal.attempt_count = 1
    terminal.result = _transform_success_result(terminal.id, payload.mode).model_dump(mode="json")
    replacement = _task(request_hash=request_hash)
    replacement.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, None, terminal, (0, 0), replacement)
    output_checks: list[TaskId] = []
    new_admissions: list[bool] = []

    async def admit_terminal_replay(task: TaskRead) -> bool:
        output_checks.append(task.id)
        return False

    def admit_new() -> None:
        new_admissions.append(True)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=terminal.owner_user_id,
        idempotency_key=IdempotencyKey(uuid.uuid7()),
        payload=payload,
        admit_new=admit_new,
        admit_terminal_replay=admit_terminal_replay,
    )

    assert reservation.created
    assert reservation.task.id == replacement.id
    assert output_checks == [terminal.id]
    assert new_admissions == [True]
    assert session.commits == 1
    assert not any(
        "INSERT INTO task_replay_keys" in str(statement) for statement in session.statements
    )
    assert "INSERT INTO tasks " in str(session.statements[-1])


async def test_terminal_output_check_failure_rolls_back_without_binding_or_replacement() -> None:
    payload = _markdown_payload()
    request_hash = task_service_module._request_hash(task_service_module._serialize(payload))
    terminal = _task(request_hash=request_hash, status=TaskStatus.SUCCEEDED)
    terminal.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    terminal.started_at = datetime.now(UTC)
    terminal.finished_at = datetime.now(UTC)
    terminal.attempt_count = 1
    terminal.result = _transform_success_result(terminal.id, payload.mode).model_dump(mode="json")
    session = _Session(True, None, None, terminal)

    async def admit_terminal_replay(_task: TaskRead) -> bool:
        message = "Provider unavailable"
        raise ConnectionError(message)

    with pytest.raises(ConnectionError, match="Provider unavailable"):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=terminal.owner_user_id,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=payload,
            admit_terminal_replay=admit_terminal_replay,
        )

    assert session.rollbacks == 1
    assert session.commits == 0
    assert not any(
        "INSERT INTO task_replay_keys" in str(statement) for statement in session.statements
    )
    assert not any("INSERT INTO tasks " in str(statement) for statement in session.statements)


async def test_terminal_replay_without_output_admission_fails_closed() -> None:
    payload = _markdown_payload()
    request_hash = task_service_module._request_hash(task_service_module._serialize(payload))
    terminal = _task(request_hash=request_hash, status=TaskStatus.SUCCEEDED)
    terminal.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    terminal.started_at = datetime.now(UTC)
    terminal.finished_at = datetime.now(UTC)
    terminal.attempt_count = 1
    terminal.result = _transform_success_result(terminal.id, payload.mode).model_dump(mode="json")
    session = _Session(True, None, None, terminal)

    with pytest.raises(RuntimeError, match="requires an admission callback"):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=terminal.owner_user_id,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=payload,
        )

    assert session.rollbacks == 1
    assert session.commits == 0
    assert not any(
        "INSERT INTO task_replay_keys" in str(statement) for statement in session.statements
    )
    assert not any("INSERT INTO tasks " in str(statement) for statement in session.statements)


async def test_markdown_task_persists_ciphertext_and_hash_without_plaintext() -> None:
    payload = _markdown_payload()
    assert isinstance(payload.source, MarkdownSource)
    serialized = task_service_module._serialize(payload)
    row = _task(request_hash=task_service_module._request_hash(serialized))
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, None, None, (0, 0), row)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=payload,
    )

    assert reservation.created is True
    statement = session.statements[-1]
    assert isinstance(statement, ClauseElement)
    params = statement.compile().params or {}
    persisted = params["payload"]
    assert isinstance(persisted, dict)
    encoded = json.dumps(persisted, sort_keys=True)
    assert payload.source.content not in encoded
    source = persisted["source"]
    assert isinstance(source, dict)
    assert source["content_sha256"] == hashlib.sha256(payload.source.content.encode()).hexdigest()
    assert source["content_encryption"] == "fernet-v1"
    assert source["content_ciphertext"] != payload.source.content
    gc_sql = str(statement)
    assert "DELETE FROM tasks" in gc_sql
    assert "tasks.expires_at <= now()" in gc_sql
    assert "tasks.status !=" in gc_sql
    assert "tasks.lease_expires_at <=" in gc_sql


def test_uploaded_markdown_task_stores_reference_without_inline_encryption() -> None:
    upload_id = uuid.uuid7()
    payload = DocumentTransformRequest(
        source=MarkdownSource(
            kind="uploaded_markdown",
            upload_id=upload_id,
            name="Review",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )
    serialized = task_service_module._serialize(payload)

    stored = task_service_module._payload_for_storage(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        payload=payload,
        serialized=serialized,
    )

    assert stored == serialized
    assert task_service_module._deserialize_document_transform_payload(stored) == payload
    source = stored["source"]
    assert isinstance(source, dict)
    assert "content_ciphertext" not in source


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("expired", [False, True])
async def test_markdown_active_hash_reuse_authorizes_before_recording_new_key(
    *, revoked: bool, expired: bool
) -> None:
    payload = _markdown_payload()
    row = _task(
        request_hash=task_service_module._request_hash(task_service_module._serialize(payload))
    )
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, (row, expired), (row, expired), None)
    admissions: list[bool] = []

    async def admit() -> None:
        admissions.append(True)
        if revoked:
            message = "revoked"
            raise PermissionError(message)

    async def reserve():
        return await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=payload,
            admit=admit,
        )

    if revoked:
        with pytest.raises(PermissionError, match="revoked"):
            await reserve()
        assert session.rollbacks == 1
        assert session.commits == 0
        assert len(session.statements) == 3
    elif expired:
        with pytest.raises(TaskTargetBusyError):
            await reserve()
        assert session.rollbacks == 1
        assert session.commits == 0
        assert len(session.statements) == 4
    else:
        reservation = await reserve()
        assert reservation.task.id == row.id
        assert not reservation.created
        assert "INSERT INTO task_replay_keys" in str(session.statements[-1])
    assert admissions == [True]
    query = session.statements[2]
    assert isinstance(query, ClauseElement)
    params = query.compile().params or {}
    assert row.owner_user_id in params.values()
    assert row.request_hash in params.values()
    assert "markdown" in params.values()
    assert "tasks.status IN" in str(query)
    assert "tasks.expires_at <= now() AS expired" in str(query)


@pytest.mark.parametrize("insert_succeeds", [False, True])
async def test_expired_transform_key_retries_identical_insert_once(
    *, insert_succeeds: bool
) -> None:
    row = _task(request_hash="0" * 64)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    session = _Session(True, None, None, None, (0, 0), None, None, row if insert_succeeds else None)
    admissions: list[bool] = []

    async def admit() -> None:
        admissions.append(True)
        assert session.commits == 0

    async def reserve():
        return await _service(session).create_or_replay(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            owner_user_id=row.owner_user_id,
            idempotency_key=row.idempotency_key,
            payload=_markdown_payload(),
            admit=admit,
        )

    if insert_succeeds:
        reservation = await reserve()
        assert reservation.created
        assert session.commits == 1
        assert session.rollbacks == 0
    else:
        with pytest.raises(TaskTargetBusyError):
            await reserve()
        assert session.commits == 0
        assert session.rollbacks == 1
    assert admissions == [True]
    assert len(session.statements) == 8
    assert session.statements[5] is session.statements[7]


async def test_worker_load_decrypts_markdown_and_accepts_legacy_payloads() -> None:
    payload = _markdown_payload()
    assert isinstance(payload.source, MarkdownSource)
    serialized = task_service_module._serialize(payload)
    row = _task(request_hash=task_service_module._request_hash(serialized))
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    stored_payload: dict[str, object] = dict(
        task_service_module._payload_for_storage(
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            payload=payload,
            serialized=serialized,
        )
    )
    row.payload = stored_payload
    encrypted_record = await _service(_Session(row)).load_document_transform(row.id)

    assert encrypted_record.payload == payload
    assert encrypted_record.payload is not None
    assert isinstance(encrypted_record.payload.source, MarkdownSource)
    assert encrypted_record.payload.source.content == payload.source.content

    legacy_payload: dict[str, object] = dict(serialized)
    row.payload = legacy_payload
    legacy_record = await _service(_Session(row)).load_document_transform(row.id)
    assert legacy_record.payload == payload

    drive_payload = _in_place_payload()
    serialized_drive: dict[str, object] = dict(task_service_module._serialize(drive_payload))
    row.payload = serialized_drive
    drive_record = await _service(_Session(row)).load_document_transform(row.id)
    assert drive_record.payload == drive_payload


async def test_create_or_replay_creates_one_canonical_task() -> None:
    # SHA-256 of the service's canonical compact/sorted JSON representation.
    row = _task(request_hash="34664c7dd3fa3e21bde1016366dfa8933e84372be6976afed70d10d4da73c7a6")
    session = _Session(row)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=_Payload(name="PRD", destination="folder-1"),
    )

    assert reservation.created is True
    assert reservation.task.id == row.id
    assert session.commits == 1
    sql = str(session.statements[0])
    assert "ON CONFLICT" in sql
    assert "owner_user_id" in sql
    assert "idempotency_key" in sql


async def test_same_key_with_changed_payload_is_rejected() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(None, row)

    with pytest.raises(TaskIdempotencyConflictError):
        await _service(session).create_or_replay(
            kind=TaskKind.FILE_SMART_CREATE,
            owner_user_id=row.owner_user_id,
            idempotency_key=row.idempotency_key,
            payload=_Payload(name="Different", destination="folder-1"),
        )


async def test_same_key_with_same_payload_replays_without_another_commit() -> None:
    row = _task(request_hash="34664c7dd3fa3e21bde1016366dfa8933e84372be6976afed70d10d4da73c7a6")
    session = _Session(None, row)

    reservation = await _service(session).create_or_replay(
        kind=TaskKind.FILE_SMART_CREATE,
        owner_user_id=row.owner_user_id,
        idempotency_key=row.idempotency_key,
        payload=_Payload(name="PRD", destination="folder-1"),
    )

    assert reservation.created is False
    assert reservation.task.id == row.id
    assert session.commits == 0


async def test_claim_is_an_atomic_conditional_transition_with_a_lease() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.RUNNING)
    row.attempt_count = 1
    row.started_at = datetime.now(UTC)
    row.lease_token = uuid.uuid7()
    row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    session = _Session(row)

    claim = await _service(session).claim(row.id, lease_duration=timedelta(minutes=1))

    assert claim.lease_token is not None
    assert session.commits == 1
    sql = str(session.statements[0])
    assert "tasks.status" in sql
    assert "tasks.lease_expires_at" in sql
    assert "tasks.expires_at > now()" in sql
    assert "greatest(tasks.expires_at" in sql.lower()
    assert "RETURNING" in sql


async def test_stale_executor_cannot_complete_a_reclaimed_task() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.RUNNING)
    session = _Session(None)

    with pytest.raises(TaskLeaseLostError):
        await _service(session).succeed(
            row.id,
            lease_token=uuid.uuid7(),
            result=_Payload(name="PRD", destination="folder-1"),
        )

    assert session.rollbacks == 1


async def test_defer_atomically_releases_retryable_running_lease() -> None:
    row = _task(request_hash="0" * 64, status=TaskStatus.RUNNING)
    row.attempt_count = 1
    row.started_at = datetime.now(UTC)
    row.lease_token = uuid.uuid7()
    row.lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    row.resource_id = FileId("already-created")
    session = _Session(row, row)

    deferred = await _service(session).defer(row.id, lease_token=uuid.uuid7())
    reclaimed = await _service(session).claim(row.id, lease_duration=timedelta(minutes=6))

    assert deferred.status == TaskStatus.RUNNING
    assert deferred.resource_id == FileId("already-created")
    assert reclaimed.task.status == TaskStatus.RUNNING
    assert session.commits == 2
    sql = str(session.statements[0])
    assert "tasks.lease_token IS NOT DISTINCT FROM" in sql
    assert "RETURNING" in sql
    statement = session.statements[0]
    assert isinstance(statement, ClauseElement)
    params = statement.compile().params or {}
    assert "payload" not in params


async def test_success_redacts_document_transform_payload_and_terminal_load_skips_decrypt() -> None:
    now = datetime.now(UTC)
    row = _task(request_hash="0" * 64, status=TaskStatus.SUCCEEDED)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    row.attempt_count = 1
    row.started_at = now
    row.finished_at = now
    result = _success_result(row.id)
    row.result = result.model_dump(mode="json")
    session = _Session(row)

    await _service(session).succeed(
        row.id,
        lease_token=uuid.uuid7(),
        result=result,
    )

    statement = session.statements[0]
    assert isinstance(statement, ClauseElement)
    params = statement.compile().params or {}
    assert task_service_module._TERMINAL_REDACTED_PAYLOAD in params.values()
    redacted_payload: dict[str, object] = {"redacted": True}
    row.payload = redacted_payload
    terminal = await _service(_Session(row)).load_document_transform(row.id)
    assert terminal.payload is None


async def test_failed_task_persists_only_bounded_public_detail() -> None:
    now = datetime.now(UTC)
    row = _task(request_hash="0" * 64, status=TaskStatus.FAILED)
    row.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    row.attempt_count = 1
    row.started_at = now
    row.finished_at = now
    row.error_code = "provider_failed"
    row.error_detail = "Task could not be completed"
    raw_provider_detail = (
        "https://www.googleapis.com/drive/v3/files/private-resource-id "
        "Bearer ya29.provider-access-token " + '{"error":{"message":"private provider body"}}' * 100
    )
    session = _Session(row)

    await _service(session).fail(
        row.id,
        lease_token=uuid.uuid7(),
        error_code="provider_failed",
        error_detail=raw_provider_detail,
    )

    statement = session.statements[0]
    assert isinstance(statement, ClauseElement)
    params = statement.compile().params or {}
    persisted = params["error_detail"]
    assert isinstance(persisted, str)
    assert persisted == "Task could not be completed"
    assert len(persisted) <= 256
    assert "private-resource-id" not in persisted
    assert "ya29.provider-access-token" not in persisted
    assert "private provider body" not in persisted
    assert task_service_module._TERMINAL_REDACTED_PAYLOAD in params.values()
    assert "expires_at" not in params


async def test_pending_task_can_be_discarded_before_execution() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(row)

    deleted = await _service(session).discard_pending(
        row.id, owner_user_id=UserId(row.owner_user_id)
    )

    assert deleted is True
    assert session.commits == 1
    sql = str(session.statements[0])
    assert "DELETE FROM tasks" in sql
    assert "tasks.status" in sql
    assert "tasks.resource_id IS NULL" in sql


async def test_task_lookup_is_owner_isolated_and_expiry_enforced() -> None:
    row = _task(request_hash="0" * 64)
    other_owner = uuid.uuid7()
    session = _Session(None)

    with pytest.raises(TaskNotFoundError):
        await _service(session).get_for_owner(row.id, UserId(other_owner))

    sql = str(session.statements[0])
    assert "tasks.id" in sql
    assert "tasks.owner_user_id" in sql
    assert "tasks.expires_at > now()" in sql


def test_database_model_carries_legal_state_and_idempotency_constraints() -> None:
    table = Task.__table__
    assert isinstance(table, Table)
    names = {constraint.name for constraint in table.constraints}

    assert "ck_tasks_legal_state" in names
    assert "uq_tasks_owner_kind_idempotency" in names


async def test_resource_recording_returns_only_identity_and_retains_lease_guard() -> None:
    row = _task(request_hash="0" * 64)
    session = _Session(row.id)

    await _service(session).record_resource(
        row.id, lease_token=uuid.uuid7(), resource_id=FileId("created-resource")
    )

    assert session.commits == 1
    sql = str(session.statements[0])
    assert "tasks.status" in sql
    assert "tasks.lease_token IS NOT DISTINCT FROM" in sql
    assert sql.partition(" RETURNING ")[2] == "tasks.id"


async def test_resource_recording_rejects_a_lost_lease_without_commit() -> None:
    session = _Session(None)
    with pytest.raises(TaskLeaseLostError):
        await _service(session).record_resource(
            TaskId(uuid.uuid7()), lease_token=uuid.uuid7(), resource_id=FileId("created-resource")
        )
    assert session.commits == 0
    assert session.rollbacks == 1


@pytest.mark.parametrize(
    ("active_count", "in_place_count", "strategy", "rejected"),
    [
        (19, 9, OutputStrategy.IN_PLACE, False),
        (20, 0, OutputStrategy.COPY, True),
        (0, 10, OutputStrategy.IN_PLACE, True),
        (0, 10, OutputStrategy.COPY, False),
    ],
)
async def test_admission_limits_share_one_count_query(
    active_count: int, in_place_count: int, strategy: OutputStrategy, *, rejected: bool
) -> None:
    session = _Session((active_count, in_place_count))
    payload = _in_place_payload().model_copy(update={"output_strategy": strategy})
    if rejected:
        with pytest.raises(TaskAdmissionLimitError):
            await _service(session)._check_document_transform_limits(uuid.uuid7(), payload)
    else:
        await _service(session)._check_document_transform_limits(uuid.uuid7(), payload)
    assert len(session.statements) == 1
    assert "FILTER" in str(session.statements[0])
    assert session.rollbacks == int(rejected)
