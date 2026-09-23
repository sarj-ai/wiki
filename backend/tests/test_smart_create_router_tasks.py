from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import override
import uuid

from fastapi import HTTPException, Response
from pydantic import BaseModel
import pytest

from app.config import settings
from app.document_transform.router import (
    create_document_transform,
    get_document_transform_capabilities,
)
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
    TransformMode,
)
from app.drive.schemas import MIME_SLIDE
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import FileId, IdempotencyKey, TaskId, UserId
from app.models.user import User
from app.smart_create.router import create_smart_file
from app.smart_create.schemas import BlankSource, SmartCreateResult, SmartFileCreate
from app.tasks.queue import TaskEnqueuer
from app.tasks.schemas import (
    DocumentTransformTaskRecord,
    PendingTaskRead,
    SmartCreateTaskRecord,
    SucceededTaskRead,
    TaskClaim,
    TaskKind,
    TaskRead,
    TaskReservation,
    TaskStatus,
)
from app.tasks.service import TaskService


def _payload() -> SmartFileCreate:
    return SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("folder"),
        name="PRD",
        emoji="📝",
        alias="product-requirements",
    )


def _result(task_id: TaskId) -> SmartCreateResult:
    return SmartCreateResult(
        task_id=task_id,
        file=FileResponse(
            id=FileId("created"),
            name="PRD",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("folder"),
            drive_id=None,
            modified_time=None,
        ),
        warnings=[],
    )


def _pending(
    task_id: TaskId,
    *,
    kind: TaskKind = TaskKind.FILE_SMART_CREATE,
) -> PendingTaskRead:
    return PendingTaskRead(
        id=task_id,
        kind=kind,
        status=TaskStatus.PENDING,
        result=None,
        resource_id=None,
        error_code=None,
        error_detail=None,
        attempt_count=0,
        created_at=datetime.now(UTC),
        started_at=None,
        finished_at=None,
    )


def _succeeded(task_id: TaskId) -> SucceededTaskRead:
    result = _result(task_id)
    return SucceededTaskRead(
        id=task_id,
        kind=TaskKind.FILE_SMART_CREATE,
        status=TaskStatus.SUCCEEDED,
        result=result,
        resource_id=result.file.id,
        error_code=None,
        error_detail=None,
        attempt_count=1,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


class _Tasks(TaskService):
    def __init__(self, *, created: bool, terminal: bool = False) -> None:
        self.task_id = TaskId(uuid.uuid7())
        self.created = created
        self.terminal = terminal
        self.requested_kind = TaskKind.FILE_SMART_CREATE
        self.discarded: list[TaskId] = []

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
        del admit_terminal_replay
        if self.created and admit is not None:
            await admit()
        if self.created and admit_new is not None:
            admit_new()
        self.requested_kind = kind
        task = _succeeded(self.task_id) if self.terminal else _pending(self.task_id, kind=kind)
        return TaskReservation(task=task, created=self.created)

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
        del owner_user_id
        self.discarded.append(task_id)
        return True

    @override
    async def cancel(self, task_id: TaskId, *, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def get_for_owner(self, task_id: TaskId, owner_user_id: uuid.UUID) -> TaskRead:
        raise NotImplementedError

    @override
    async def load_smart_create(self, task_id: TaskId) -> SmartCreateTaskRecord:
        raise NotImplementedError

    @override
    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord:
        raise NotImplementedError


class _Enqueuer(TaskEnqueuer):
    def __init__(self) -> None:
        self.task_ids: list[TaskId] = []
        self.document_task_ids: list[TaskId] = []

    @override
    async def enqueue_smart_create(self, task_id: TaskId) -> None:
        self.task_ids.append(task_id)

    @override
    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        self.document_task_ids.append(task_id)


class _DestinationFiles:
    def __init__(self, destination: FileResponse) -> None:
        self.destination = destination
        self.requested: list[FileId] = []

    async def get(self, file_id: FileId) -> FileResponse:
        self.requested.append(file_id)
        return self.destination


class _UnexpectedDestinationRead:
    async def get(self, file_id: FileId) -> FileResponse:
        del file_id
        pytest.fail("an idempotent replay must not revalidate its destination")


def _destination_files(
    *,
    mime_type: str = "application/vnd.google-apps.folder",
    can_add_children: bool = True,
) -> _DestinationFiles:
    return _DestinationFiles(
        FileResponse(
            id=FileId("folder"),
            name="Folder",
            mime_type=mime_type,
            parent_folder_id=None,
            drive_id=None,
            modified_time="time",
            can_add_children=can_add_children,
        )
    )


def _user() -> User:
    user = User()
    user.id = UserId(uuid.uuid7())
    return user


async def _create_transform(
    payload: DocumentTransformRequest,
    *,
    tasks: _Tasks,
    enqueuer: _Enqueuer,
    files: _DestinationFiles | _UnexpectedDestinationRead,
    response: Response | None = None,
) -> TaskRead:
    return await create_document_transform(
        payload=payload,
        idempotency_key=uuid.uuid7(),
        response=response or Response(),
        user=_user(),
        tasks=tasks,
        enqueuer=enqueuer,
        files=files,
    )


@pytest.mark.parametrize("created", [True, False])
async def test_markdown_transform_response_points_to_recoverable_task(created: bool) -> None:
    tasks = _Tasks(created=created)
    response = Response()
    payload = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# Decision\n\nUse the existing workflow.",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.BOTH,
    )

    task = await _create_transform(
        payload,
        tasks=tasks,
        enqueuer=_Enqueuer(),
        files=_destination_files(),
        response=response,
    )

    assert response.headers["Location"] == f"/tasks/{task.id}"
    assert response.headers["Idempotency-Replayed"] == str(not created).lower()
    assert response.headers["Retry-After"] == "1"
    assert response.headers["Cache-Control"] == "no-store"


async def test_new_task_returns_pending_immediately_and_enqueues() -> None:
    tasks = _Tasks(created=True)
    enqueuer = _Enqueuer()
    response = Response()

    task = await create_smart_file(
        payload=_payload(),
        idempotency_key=uuid.uuid7(),
        response=response,
        user=_user(),
        tasks=tasks,
        enqueuer=enqueuer,
    )

    assert isinstance(task, PendingTaskRead)
    assert enqueuer.task_ids == [task.id]
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.headers["Retry-After"] == "1"


@pytest.mark.parametrize(
    ("mime_type", "can_add_children"),
    [
        ("application/vnd.google-apps.document", True),
        ("application/vnd.google-apps.folder", False),
    ],
)
async def test_markdown_transform_rejects_invalid_destination_before_task_creation(
    mime_type: str,
    can_add_children: bool,
) -> None:
    tasks = _Tasks(created=True)
    payload = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="Draft",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )

    files = _destination_files(
        mime_type=mime_type,
        can_add_children=can_add_children,
    )
    with pytest.raises(FileNotInScopeError, match="Destination does not accept new files"):
        await _create_transform(
            payload,
            tasks=tasks,
            enqueuer=_Enqueuer(),
            files=files,
        )

    assert tasks.requested_kind is TaskKind.FILE_SMART_CREATE
    assert tasks.discarded == []
    assert files.requested == [FileId("folder")]


async def test_markdown_transform_replay_skips_changed_destination_validation() -> None:
    tasks = _Tasks(created=False)
    enqueuer = _Enqueuer()
    payload = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="Draft",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )

    task = await _create_transform(
        payload,
        tasks=tasks,
        enqueuer=enqueuer,
        files=_UnexpectedDestinationRead(),
    )

    assert task.id == tasks.task_id
    assert enqueuer.document_task_ids == [task.id]


async def test_pending_replay_reenqueues_deterministically() -> None:
    tasks = _Tasks(created=False)
    enqueuer = _Enqueuer()

    task = await create_smart_file(
        payload=_payload(),
        idempotency_key=uuid.uuid7(),
        response=Response(),
        user=_user(),
        tasks=tasks,
        enqueuer=enqueuer,
    )

    assert enqueuer.task_ids == [task.id]


async def test_succeeded_replay_returns_typed_result_without_enqueue() -> None:
    tasks = _Tasks(created=False, terminal=True)
    enqueuer = _Enqueuer()

    task = await create_smart_file(
        payload=_payload(),
        idempotency_key=uuid.uuid7(),
        response=Response(),
        user=_user(),
        tasks=tasks,
        enqueuer=enqueuer,
    )

    assert isinstance(task, SucceededTaskRead)
    assert task.result.file.id == FileId("created")
    assert enqueuer.task_ids == []


async def test_document_transform_endpoint_creates_separate_queued_task() -> None:
    tasks = _Tasks(created=True)
    enqueuer = _Enqueuer()
    response = Response()
    payload = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# Decision\n\nThe team approved option B.",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )

    task = await _create_transform(
        payload,
        tasks=tasks,
        enqueuer=enqueuer,
        files=_destination_files(),
        response=response,
    )

    assert task.kind is TaskKind.FILE_DOCUMENT_TRANSFORM
    assert tasks.requested_kind is TaskKind.FILE_DOCUMENT_TRANSFORM
    assert enqueuer.document_task_ids == [task.id]
    assert enqueuer.task_ids == []
    assert response.headers["Idempotency-Replayed"] == "false"


async def test_markdown_transform_rejects_unsupported_syntax_before_task_creation() -> None:
    tasks = _Tasks(created=True)
    enqueuer = _Enqueuer()
    files = _destination_files()
    payload = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="![diagram](https://example.com/diagram.png)",
            name="Unsupported",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )

    with pytest.raises(HTTPException) as raised:
        await _create_transform(payload, tasks=tasks, enqueuer=enqueuer, files=files)

    assert raised.value.status_code == 422
    assert tasks.requested_kind is TaskKind.FILE_SMART_CREATE
    assert enqueuer.document_task_ids == []
    assert files.requested == []


async def test_document_transform_capability_and_kill_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_IN_PLACE_ENABLED", False)
    capability = await get_document_transform_capabilities(_user())
    assert capability.in_place is False
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="time",
        ),
        mode=TransformMode.UNSLOP,
        output_strategy=OutputStrategy.IN_PLACE,
    )
    with pytest.raises(HTTPException, match="disabled"):
        await _create_transform(
            payload,
            tasks=_Tasks(created=True),
            enqueuer=_Enqueuer(),
            files=_destination_files(),
        )


async def test_native_slides_in_place_is_rejected_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_IN_PLACE_ENABLED", True)
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="time",
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )
    tasks = _Tasks(created=True)
    enqueuer = _Enqueuer()

    with pytest.raises(HTTPException) as exc_info:
        await _create_transform(
            payload,
            tasks=tasks,
            enqueuer=enqueuer,
            files=_destination_files(mime_type=MIME_SLIDE),
        )

    assert exc_info.value.status_code == 422
    assert not tasks.discarded
    assert enqueuer.document_task_ids == []
