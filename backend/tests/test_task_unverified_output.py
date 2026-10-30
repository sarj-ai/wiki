from datetime import UTC, datetime
import uuid

from fastapi import Response
import pytest

from app.document_transform.schemas import (
    CopyCheckpoint,
    DocumentOutput,
    DocumentTransformRequest,
    DriveFileSource,
    InPlaceCheckpoint,
    OutputStrategy,
    TransformMode,
)
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import FileId, TaskId, UserId
from app.models.user import User
from app.tasks.errors import TaskNotFoundError
from app.tasks.router import get_task
from app.tasks.schemas import DocumentTransformTaskRecord, FailedTaskRead, TaskKind, TaskStatus


class TaskReaderStub:
    def __init__(self, record: DocumentTransformTaskRecord, hidden: bool) -> None:
        self.record = record
        self.hidden = hidden
        self.loads = 0

    async def get_for_owner(self, task_id: TaskId, owner_user_id: UserId) -> FailedTaskRead:
        if self.hidden:
            raise TaskNotFoundError
        assert task_id == self.record.task.id
        assert isinstance(self.record.task, FailedTaskRead)
        return self.record.task

    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord:
        self.loads += 1
        assert task_id == self.record.task.id
        return self.record


class OutputReaderStub:
    def __init__(self, output: FileResponse, revoked: bool) -> None:
        self.output = output
        self.revoked = revoked
        self.requests: list[FileId] = []

    async def get(self, file_id: FileId) -> FileResponse:
        self.requests.append(file_id)
        if self.revoked:
            raise FileNotInScopeError
        return self.output


def _failed_task(
    *,
    resource_id: FileId | None = None,
    error_code: str = "output_verification_failed",
) -> FailedTaskRead:
    now = datetime.now(UTC)
    return FailedTaskRead(
        id=TaskId(uuid.uuid7()),
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        status=TaskStatus.FAILED,
        result=None,
        resource_id=resource_id,
        error_code=error_code,
        error_detail="Task could not be completed",
        attempt_count=1,
        created_at=now,
        started_at=now,
        finished_at=now,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    [
        "visible",
        "redacted",
        "revoked",
        "wrong_owner",
        "source",
        "missing",
        "hidden_task",
        "in_place",
        "wrong_id",
    ],
)
async def test_failed_copy_review_descriptor_checks_owner_and_live_access(scenario: str) -> None:
    owner = uuid.uuid7()
    user = User(id=owner, email="qa@example.com")
    task = _failed_task()
    output_id = FileId("source" if scenario == "source" else "known-copy")
    output = DocumentOutput(
        file_id=output_id, name="Private title", parent_folder_id=FileId("parent")
    )
    checkpoint = CopyCheckpoint.model_construct(output=output)
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-04T00:00:00Z",
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.COPY,
    )
    record = DocumentTransformTaskRecord.model_construct(
        owner_user_id=uuid.uuid7() if scenario == "wrong_owner" else owner,
        task=task,
        payload=None
        if scenario == "redacted"
        else payload.model_copy(update={"output_strategy": OutputStrategy.IN_PLACE})
        if scenario == "in_place"
        else payload,
        checkpoint=None if scenario == "missing" else checkpoint,
    )
    service = TaskReaderStub(record, scenario == "hidden_task")
    files = OutputReaderStub(
        FileResponse(
            id=FileId("unexpected") if scenario == "wrong_id" else output_id,
            name="Live private title",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("parent"),
            drive_id=None,
            modified_time=None,
        ),
        scenario == "revoked",
    )
    if scenario == "hidden_task":
        with pytest.raises(TaskNotFoundError):
            await get_task(task.id, service, user, Response(), files)
        assert service.loads == 0
        assert files.requests == []
        return
    result = await get_task(task.id, service, user, Response(), files)
    assert isinstance(result, FailedTaskRead)
    assert result.result is None
    if scenario in {"visible", "redacted"}:
        assert result.unverified_output is not None
        assert result.unverified_output.model_dump() == {"file_id": "known-copy"}
        assert files.requests == [output_id]
    else:
        assert result.unverified_output is None
    if scenario in {"wrong_owner", "source", "missing", "in_place"}:
        assert files.requests == []


@pytest.mark.parametrize(
    "scenario",
    [
        "visible",
        "redacted",
        "revoked",
        "wrong_owner",
        "wrong_resource",
        "wrong_error",
        "wrong_id",
        "wrong_payload_source",
    ],
)
async def test_post_mutation_failure_exposes_only_owner_verified_original(
    scenario: str,
) -> None:
    owner = uuid.uuid7()
    user = User(id=owner, email="qa@example.com")
    source_id = FileId("original-document")
    task = _failed_task(
        resource_id=FileId("different-resource") if scenario == "wrong_resource" else source_id,
        error_code="needs_review" if scenario == "wrong_error" else "in_place_verification_failed",
    )
    checkpoint = InPlaceCheckpoint.model_construct(document_id=source_id)
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("other-source") if scenario == "wrong_payload_source" else source_id,
            expected_modified_time="2026-09-04T00:00:00Z",
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )
    record = DocumentTransformTaskRecord.model_construct(
        owner_user_id=uuid.uuid7() if scenario == "wrong_owner" else owner,
        task=task,
        payload=None if scenario == "redacted" else payload,
        checkpoint=checkpoint,
    )
    service = TaskReaderStub(record, False)
    files = OutputReaderStub(
        FileResponse(
            id=FileId("unexpected") if scenario == "wrong_id" else source_id,
            name="Original document",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("parent"),
            drive_id=None,
            modified_time=None,
        ),
        scenario == "revoked",
    )

    result = await get_task(task.id, service, user, Response(), files)

    assert isinstance(result, FailedTaskRead)
    assert (result.unverified_output is not None) is (scenario in {"visible", "redacted"})
    assert files.requests == (
        [source_id] if scenario in {"visible", "redacted", "revoked", "wrong_id"} else []
    )
