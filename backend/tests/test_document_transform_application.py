from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import uuid

from fastapi import HTTPException
from pydantic import BaseModel
import pytest

from app.config import settings
from app.document_transform.application import submit_document_transform
from app.document_transform.schemas import (
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
)
from app.drive.schemas import MIME_DOC
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import FileId, IdempotencyKey, TaskId
from app.tasks.errors import (
    TaskAdmissionLimitError,
    TaskEnqueueError,
    TaskIdempotencyConflictError,
    TaskTargetBusyError,
)
from app.tasks.schemas import TaskKind, TaskReservation
from tests.support import dependency


def payload(strategy: OutputStrategy = OutputStrategy.COPY) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="unchanged-time"
        ),
        mode=TransformMode.FORMAT,
        output_strategy=strategy,
    )


class SubmissionHarness:
    def __init__(self, *, created: bool = True, status: str = "pending") -> None:
        now = datetime.now(UTC)
        self.reservation = TaskReservation.model_validate(
            {
                "created": created,
                "task": {
                    "id": TaskId(uuid.uuid7()),
                    "kind": TaskKind.FILE_DOCUMENT_TRANSFORM,
                    "status": status,
                    "created_at": now,
                    "result": None,
                    "resource_id": None,
                    "error_code": None,
                    "error_detail": None,
                    "attempt_count": 1 if status == "running" else 0,
                    "started_at": now if status == "running" else None,
                    "finished_at": now if status == "cancelled" else None,
                },
            }
        )
        self.request = payload()
        self.owner = uuid.uuid7()
        self.key = IdempotencyKey(uuid.uuid7())
        self.calls: list[str] = []
        self.error: Exception | None = None
        self.enqueue_error: Exception | None = None
        self.file_error: Exception | None = None

    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: uuid.UUID,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
        admit: Callable[[], Awaitable[None]],
    ) -> TaskReservation:
        self.calls.append("reserve")
        assert kind is TaskKind.FILE_DOCUMENT_TRANSFORM
        assert owner_user_id == self.owner
        assert idempotency_key == self.key
        assert payload is self.request
        if self.error is not None:
            raise self.error
        if self.reservation.created:
            await admit()
        return self.reservation

    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        self.calls.append("enqueue")
        assert task_id == self.reservation.task.id
        if self.enqueue_error is not None:
            raise self.enqueue_error

    async def get(self, file_id: FileId) -> FileResponse:
        self.calls.append("file")
        assert file_id == "source"
        if self.file_error is not None:
            raise self.file_error
        return FileResponse(
            id=file_id,
            name="Source",
            mime_type=MIME_DOC,
            parent_folder_id=None,
            drive_id=None,
            modified_time=None,
        )


@pytest.mark.parametrize("created", [True, False])
@pytest.mark.parametrize("status", ["pending", "running", "cancelled"])
async def test_identity_admission_replay_and_active_only_dispatch(
    created: bool, status: str
) -> None:
    harness = SubmissionHarness(created=created, status=status)
    result = await submit_document_transform(
        payload=harness.request,
        owner_user_id=harness.owner,
        idempotency_key=harness.key,
        tasks=dependency(harness),
        enqueuer=dependency(harness),
        files=harness,
    )
    assert result.task is harness.reservation.task
    assert result.replayed is (not created)
    assert result.retry_after == 1
    expected = ["reserve"]
    if created:
        expected.append("file")
    if status in {"pending", "running"}:
        expected.append("enqueue")
    assert harness.calls == expected


@pytest.mark.parametrize(
    "error_type", [TaskAdmissionLimitError, TaskIdempotencyConflictError, TaskTargetBusyError]
)
async def test_service_rejections_propagate_without_enqueue(error_type: type[Exception]) -> None:
    harness = SubmissionHarness()
    harness.error = error_type()
    with pytest.raises(error_type) as caught:
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )
    assert caught.value is harness.error
    assert harness.calls == ["reserve"]


async def test_enqueue_failure_does_not_discard_retained_admission() -> None:
    harness = SubmissionHarness()
    harness.enqueue_error = TaskEnqueueError()
    with pytest.raises(TaskEnqueueError):
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )
    assert harness.calls == ["reserve", "file", "enqueue"]


async def test_disabled_in_place_rejects_before_any_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_IN_PLACE_ENABLED", False)
    harness = SubmissionHarness()
    harness.request = payload(OutputStrategy.IN_PLACE)
    with pytest.raises(HTTPException) as caught:
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )
    assert caught.value.status_code == 503
    assert harness.calls == []


async def test_scope_rejection_prevents_dispatch() -> None:
    harness = SubmissionHarness()
    harness.file_error = FileNotInScopeError("Denied")
    with pytest.raises(FileNotInScopeError):
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )
    assert harness.calls == ["reserve", "file"]
