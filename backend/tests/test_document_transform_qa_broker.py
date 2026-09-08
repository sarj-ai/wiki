from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import override
import uuid

from pydantic import BaseModel
import pytest

from app.document_transform.qa_broker import (
    authorize_qa_canary_output,
    authorize_qa_canary_scope,
    plan_qa_canary,
    read_submitted_qa_canary_task,
    submit_planned_qa_canary,
)
from app.document_transform.qa_grants import QaAuthority, QaGrantError, QaSubmittedCanary
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
)
from app.drive.schemas import MIME_DOC, MIME_FOLDER, DriveNode
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, IdempotencyKey, QaPlanId, TaskId, UserId
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import (
    DocumentTransformAdmission,
    PendingTaskRead,
    SmartCreateTaskRecord,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
    TaskStatus,
)
from app.tasks.service import TaskService


_SOURCE = FileId("source-folder")
_DESTINATION = FileId("destination-folder")
_DRIVE = DriveId("personal-drive")


def _authority() -> QaAuthority:
    return QaAuthority(
        grant_id=DocumentTransformQaGrantId(uuid.UUID("11111111-1111-4111-8111-111111111111")),
        user_id=UserId(uuid.UUID("22222222-2222-4222-8222-222222222222")),
        user_email="qa@example.test",
        execution_name="job/executions/one",
        release_sha="a" * 40,
        image_digest="sha256:" + "b" * 64,
        source_folder_id=_SOURCE,
        destination_folder_id=_DESTINATION,
        mode=TransformMode.BOTH,
        expires_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
        job_expires_at=datetime(2026, 9, 7, 13, tzinfo=UTC),
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
        if file_id == FileId("doc-1"):
            return FileResponse(
                id=file_id,
                name="Document doc-1",
                mime_type=MIME_DOC,
                parent_folder_id=_SOURCE,
                drive_id=_DRIVE,
                modified_time="2026-09-07T12:00:00Z",
            )
        if file_id == FileId("output-1"):
            return FileResponse(
                id=file_id,
                name="Polished",
                mime_type=MIME_DOC,
                parent_folder_id=_DESTINATION,
                drive_id=_DRIVE,
                modified_time="2026-09-07T12:05:00Z",
                can_edit=True,
            )
        return FileResponse(
            id=file_id,
            name="Destination",
            mime_type=MIME_FOLDER,
            parent_folder_id=None,
            drive_id=_DRIVE,
            modified_time=None,
            can_add_children=True,
        )


class _Drive:
    def __init__(self, children: dict[FileId, list[DriveNode]]) -> None:
        self._children = children

    async def list_folder_children_bounded(
        self,
        folder_id: FileId,
        *,
        max_pages: int,
        max_children: int,
    ) -> list[DriveNode]:
        assert max_pages > 0
        assert max_children > 0
        return self._children.get(folder_id, [])


class _Tasks(TaskService):
    def __init__(self) -> None:
        self.payloads: list[DocumentTransformRequest] = []
        self.task_id = TaskId(uuid.UUID("33333333-3333-4333-8333-333333333333"))
        self.read_keys: list[tuple[uuid.UUID, IdempotencyKey]] = []

    @override
    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
        admit: Callable[[], Awaitable[None]] | None = None,
        admit_new: Callable[[], None] | None = None,
        admit_terminal_replay: Callable[[TaskRead], Awaitable[bool]] | None = None,
    ) -> TaskReservation:
        del kind, owner_user_id, idempotency_key, admit_terminal_replay
        assert isinstance(payload, DocumentTransformRequest)
        if admit is not None:
            await admit()
        if admit_new is not None:
            admit_new()
        created = not self.payloads
        self.payloads.append(payload)
        return TaskReservation(
            task=PendingTaskRead(
                id=self.task_id,
                kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                status=TaskStatus.PENDING,
                result=None,
                resource_id=None,
                error_code=None,
                error_detail=None,
                attempt_count=0,
                started_at=None,
                finished_at=None,
                created_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
            ),
            created=created,
        )

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        raise NotImplementedError

    @override
    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def record_resource(
        self, task_id: TaskId, *, lease_token: uuid.UUID, resource_id: FileId
    ) -> None:
        raise NotImplementedError

    @override
    async def succeed(
        self, task_id: TaskId, *, lease_token: uuid.UUID, result: BaseModel
    ) -> TaskRead:
        raise NotImplementedError

    @override
    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead:
        raise NotImplementedError

    @override
    async def discard_pending(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> bool:
        raise NotImplementedError

    @override
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def get_for_owner(self, task_id: TaskId, owner_user_id: uuid.UUID) -> TaskRead:
        assert task_id == self.task_id
        assert owner_user_id == _authority().user_id
        return PendingTaskRead(
            id=self.task_id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            status=TaskStatus.PENDING,
            result=None,
            resource_id=None,
            error_code=None,
            error_detail=None,
            attempt_count=0,
            started_at=None,
            finished_at=None,
            created_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
        )

    @override
    async def get_for_owner_bounded_result(
        self,
        task_id: TaskId,
        owner_user_id: uuid.UUID,
        *,
        max_result_bytes: int,
        max_findings: int,
        max_model_entries: int,
        max_tool_entries: int,
    ) -> TaskRead:
        assert min(max_result_bytes, max_findings, max_model_entries, max_tool_entries) > 0
        return await self.get_for_owner(task_id, owner_user_id)

    @override
    async def get_document_transform_admission(
        self, owner_user_id: uuid.UUID, idempotency_key: IdempotencyKey
    ) -> DocumentTransformAdmission:
        self.read_keys.append((owner_user_id, idempotency_key))
        return DocumentTransformAdmission(task_id=self.task_id, status=TaskStatus.PENDING)

    @override
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        raise NotImplementedError


class _Enqueuer(TaskEnqueuer):
    def __init__(self) -> None:
        self.task_ids: list[TaskId] = []

    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        raise NotImplementedError

    @override
    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        self.task_ids.append(task_id)


def _node(
    file_id: str,
    *,
    mime_type: str = MIME_DOC,
    parent_id: FileId = _SOURCE,
    modified_time: str | None = "2026-09-07T12:00:00Z",
) -> DriveNode:
    return DriveNode(
        id=FileId(file_id),
        name=f"Document {file_id}",
        mime_type=mime_type,
        parent_id=parent_id,
        modified_time=modified_time,
    )


async def test_plan_selects_only_server_discovered_doc_and_forces_copy() -> None:
    authority = _authority()
    drive = _Drive({_SOURCE: [_node("doc-1")]})

    first = await plan_qa_canary(authority, drive=drive, files=_Files())
    replay = await plan_qa_canary(authority, drive=drive, files=_Files())

    assert first == replay
    assert first.source_file_id == "doc-1"
    assert first.source_modified_time == "2026-09-07T12:00:00Z"
    assert first.output_strategy is OutputStrategy.COPY
    assert first.plan_id == first.idempotency_key


@pytest.mark.parametrize(
    "children",
    [
        [],
        [_node("doc-1"), _node("doc-2")],
        [_node("doc-1", modified_time=None)],
        [_node("loop", mime_type=MIME_FOLDER, parent_id=_SOURCE)],
    ],
    ids=("empty", "ambiguous", "missing-revision", "cycle"),
)
async def test_plan_rejects_ambiguous_incomplete_or_cyclic_source(
    children: list[DriveNode],
) -> None:
    drive = _Drive(
        {
            _SOURCE: children,
            FileId("loop"): [_node("loop", mime_type=MIME_FOLDER, parent_id=FileId("loop"))],
        }
    )

    with pytest.raises(QaGrantError):
        await plan_qa_canary(_authority(), drive=drive, files=_Files())


async def test_plan_key_changes_when_source_version_changes() -> None:
    first = await plan_qa_canary(
        _authority(), drive=_Drive({_SOURCE: [_node("doc-1")]}), files=_Files()
    )
    changed = await plan_qa_canary(
        _authority(),
        drive=_Drive({_SOURCE: [_node("doc-1", modified_time="2026-09-07T12:01:00Z")]}),
        files=_Files(),
    )

    assert first.plan_id != changed.plan_id


async def test_submit_recomputes_plan_and_forces_server_bound_copy() -> None:
    authority = _authority()
    drive = _Drive({_SOURCE: [_node("doc-1")]})
    files = _Files()
    plan = await plan_qa_canary(authority, drive=drive, files=files)
    task_store = _Tasks()
    queue = _Enqueuer()

    first = await submit_planned_qa_canary(
        authority,
        plan_id=plan.plan_id,
        drive=drive,
        files=files,
        tasks=task_store,
        enqueuer=queue,
    )
    replay = await submit_planned_qa_canary(
        authority,
        plan_id=plan.plan_id,
        drive=drive,
        files=files,
        tasks=task_store,
        enqueuer=queue,
    )

    assert not first.replayed
    assert replay.replayed
    assert first.task.id == replay.task.id
    assert queue.task_ids == [first.task.id, first.task.id]
    payload = task_store.payloads[0]
    assert payload.output_strategy is OutputStrategy.COPY
    assert payload.copy_destination_folder_id == _DESTINATION
    assert isinstance(payload.source, DriveFileSource)
    assert payload.source.file_id == "doc-1"
    assert payload.source.expected_modified_time == "2026-09-07T12:00:00Z"
    assert payload.mode is authority.mode


async def test_submit_rejects_stale_or_forged_plan_before_task_admission() -> None:
    task_store = _Tasks()
    with pytest.raises(QaGrantError):
        await submit_planned_qa_canary(
            _authority(),
            plan_id=QaPlanId(uuid.UUID("99999999-9999-4999-8999-999999999999")),
            drive=_Drive({_SOURCE: [_node("doc-1")]}),
            files=_Files(),
            tasks=task_store,
            enqueuer=_Enqueuer(),
        )
    assert task_store.payloads == []


async def test_read_resolves_only_current_plan_and_owner_scoped_task() -> None:
    authority = _authority()
    plan = await plan_qa_canary(
        authority,
        drive=_Drive({_SOURCE: [_node("doc-1")]}),
        files=_Files(),
    )
    tasks = _Tasks()

    task = await read_submitted_qa_canary_task(
        authority,
        submission=QaSubmittedCanary(plan_id=plan.plan_id, task_id=tasks.task_id),
        tasks=tasks,
    )

    assert task.id == tasks.task_id
    assert tasks.read_keys == [(authority.user_id, IdempotencyKey(plan.plan_id))]


async def test_read_rejects_cross_run_task_binding_before_owner_read() -> None:
    authority = _authority()
    plan = await plan_qa_canary(
        authority,
        drive=_Drive({_SOURCE: [_node("doc-1")]}),
        files=_Files(),
    )
    tasks = _Tasks()

    with pytest.raises(QaGrantError):
        await read_submitted_qa_canary_task(
            authority,
            submission=QaSubmittedCanary(
                plan_id=plan.plan_id,
                task_id=TaskId(uuid.UUID("99999999-9999-4999-8999-999999999999")),
            ),
            tasks=tasks,
        )

    assert tasks.read_keys == [(authority.user_id, IdempotencyKey(plan.plan_id))]


@pytest.mark.parametrize(
    "current_children",
    [
        [_node("doc-1", modified_time="2026-09-07T12:01:00Z")],
        [_node("doc-1"), _node("doc-2")],
        [],
        [_node("loop", mime_type=MIME_FOLDER)],
    ],
    ids=("edited", "added", "removed", "topology-changed"),
)
async def test_submitted_read_survives_source_folder_churn(
    current_children: list[DriveNode],
) -> None:
    authority = _authority()
    original = await plan_qa_canary(
        authority,
        drive=_Drive({_SOURCE: [_node("doc-1")]}),
        files=_Files(),
    )
    tasks = _Tasks()
    await authorize_qa_canary_scope(authority, files=_Files())

    task = await read_submitted_qa_canary_task(
        authority,
        submission=QaSubmittedCanary(plan_id=original.plan_id, task_id=tasks.task_id),
        tasks=tasks,
    )

    assert task.id == tasks.task_id
    try:
        current = await plan_qa_canary(
            authority,
            drive=_Drive({_SOURCE: current_children}),
            files=_Files(),
        )
    except QaGrantError:
        return
    assert current.plan_id != original.plan_id


async def test_output_read_requires_current_exact_destination_parent() -> None:
    output = DocumentOutput(
        file_id=FileId("output-1"),
        name="Ignored Stored Name",
        parent_folder_id=_DESTINATION,
    )

    current = await authorize_qa_canary_output(_authority(), output=output, files=_Files())

    assert current.id == output.file_id


@pytest.mark.parametrize("failure", ["deleted", "moved", "revoked"])
async def test_output_read_rejects_deleted_moved_or_revoked_output(failure: str) -> None:
    class OutputFiles(_Files):
        @override
        async def get(self, file_id: FileId) -> FileResponse:
            if file_id != FileId("output-1"):
                return await super().get(file_id)
            if failure in {"deleted", "revoked"}:
                raise QaGrantError
            return FileResponse(
                id=file_id,
                name="Polished",
                mime_type=MIME_DOC,
                parent_folder_id=FileId("other-folder"),
                drive_id=_DRIVE,
                modified_time="2026-09-07T12:05:00Z",
            )

    with pytest.raises(QaGrantError):
        await authorize_qa_canary_output(
            _authority(),
            output=DocumentOutput(
                file_id=FileId("output-1"),
                name="Polished",
                parent_folder_id=_DESTINATION,
            ),
            files=OutputFiles(),
        )
