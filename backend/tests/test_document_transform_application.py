import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from threading import Event
import uuid

from fastapi import HTTPException
from pydantic import BaseModel
import pytest

from app.config import settings
from app.document_transform import application
from app.document_transform.application import (
    SOURCE_CHANGED_ERROR_CODE,
    get_current_document_polish,
    retained_transform_output_available,
    submit_document_transform,
)
from app.document_transform.errors import DocumentGatewayError
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
    TransformMode,
    ValidationSummary,
)
from app.document_transform.versions import DOCUMENT_PIPELINE_VERSION
from app.drive.schemas import MIME_DOC, MIME_FOLDER
from app.files.errors import DriveUpstreamError, FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import DocsRevisionId, FileId, IdempotencyKey, TaskId, UserId
from app.tasks.errors import (
    TaskAdmissionLimitError,
    TaskEnqueueError,
    TaskIdempotencyConflictError,
    TaskTargetBusyError,
)
from app.tasks.schemas import TaskKind, TaskRead, TaskReservation
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
    def __init__(
        self,
        *,
        created: bool = True,
        status: str = "pending",
        reauthorize_replay: bool = False,
        strategy: OutputStrategy = OutputStrategy.COPY,
    ) -> None:
        now = datetime.now(UTC)
        task_id = TaskId(uuid.uuid7())
        result = None
        if status == "succeeded":
            result = DocumentTransformResult(
                operation_id=task_id,
                source_sha256="0" * 64,
                output=DocumentOutput(
                    file_id=FileId("output"),
                    name="Source",
                    parent_folder_id=FileId("folder"),
                ),
                mode=TransformMode.FORMAT,
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
                output_strategy=strategy,
            ).model_dump(mode="json")
        self.reservation = TaskReservation.model_validate(
            {
                "created": created,
                "task": {
                    "id": task_id,
                    "kind": TaskKind.FILE_DOCUMENT_TRANSFORM,
                    "status": status,
                    "created_at": now,
                    "result": result,
                    "resource_id": None,
                    "error_code": None,
                    "error_detail": None,
                    "attempt_count": 1 if status in {"running", "succeeded"} else 0,
                    "started_at": now if status in {"running", "succeeded"} else None,
                    "finished_at": now if status in {"cancelled", "succeeded"} else None,
                },
            }
        )
        self.request = payload(strategy)
        self.owner = UserId(uuid.uuid7())
        self.key = IdempotencyKey(uuid.uuid7())
        self.calls: list[str] = []
        self.error: Exception | None = None
        self.enqueue_error: Exception | None = None
        self.file_error: Exception | None = None
        self.output_file_error: Exception | None = None
        self.source_mime_type = MIME_DOC
        self.source_modified_time = "unchanged-time"
        self.destination_can_add_children = True
        self.reauthorize_replay = reauthorize_replay
        self.current_result: DocumentTransformResult | None = None
        self.current_revision_id = "revision-current"
        self.current_revision_error = False

    async def get_current_in_place_result(
        self,
        owner_user_id: UserId,
        file_id: FileId,
        mode: TransformMode,
        revision_id: DocsRevisionId,
    ) -> DocumentTransformResult | None:
        self.calls.append(f"current:{owner_user_id}:{file_id}:{mode}:{revision_id}")
        return self.current_result

    async def current_revision(self, file_id: FileId) -> DocsRevisionId:
        if self.current_revision_error:
            raise DocumentGatewayError
        self.calls.append(f"revision:{file_id}")
        return DocsRevisionId(self.current_revision_id)

    async def create_or_replay(
        self,
        *,
        kind: TaskKind,
        owner_user_id: UserId,
        idempotency_key: IdempotencyKey,
        payload: BaseModel,
        admit: Callable[[], Awaitable[None]],
        admit_new: Callable[[], None] | None = None,
        admit_terminal_replay: Callable[[TaskRead], Awaitable[bool]] | None = None,
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
            if admit_new is not None:
                admit_new()
        elif self.reauthorize_replay:
            await admit()
            if self.reservation.task.status == "succeeded":
                assert admit_terminal_replay is not None
                assert await admit_terminal_replay(self.reservation.task)
        return self.reservation

    async def enqueue_document_transform(self, task_id: TaskId) -> None:
        self.calls.append("enqueue")
        assert task_id == self.reservation.task.id
        if self.enqueue_error is not None:
            raise self.enqueue_error

    async def get(self, file_id: FileId) -> FileResponse:
        self.calls.append("file")
        if self.file_error is not None:
            raise self.file_error
        if file_id == "output":
            if self.output_file_error is not None:
                raise self.output_file_error
            return FileResponse(
                id=file_id,
                name="Polished source",
                mime_type=MIME_DOC,
                parent_folder_id=FileId("folder"),
                drive_id=None,
                modified_time="output-time",
            )
        if file_id == "destination":
            return FileResponse(
                id=file_id,
                name="Destination",
                mime_type=MIME_FOLDER,
                parent_folder_id=None,
                drive_id=None,
                modified_time=None,
                can_add_children=self.destination_can_add_children,
            )
        assert file_id == "source"
        return FileResponse(
            id=file_id,
            name="Source",
            mime_type=self.source_mime_type,
            parent_folder_id=None,
            drive_id=None,
            modified_time=self.source_modified_time,
        )


async def test_markdown_admission_keeps_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SubmissionHarness()
    harness.request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# Report\n\nBody",
            name="Report",
            destination_folder_id=FileId("destination"),
        ),
        mode=TransformMode.BOTH,
    )
    started = Event()
    release = Event()

    def slow_validation(_content: str, *, title: str) -> None:
        assert title == "Report"
        started.set()
        assert release.wait(2)

    monkeypatch.setattr(application, "validate_markdown_import", slow_validation)
    submission = asyncio.create_task(
        submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        assert not submission.done()
    finally:
        release.set()
    result = await submission
    assert result.task is harness.reservation.task


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


async def test_terminal_semantic_replay_is_never_enqueued() -> None:
    harness = SubmissionHarness(
        created=False,
        status="succeeded",
        reauthorize_replay=True,
    )
    result = await submit_document_transform(
        payload=harness.request,
        owner_user_id=harness.owner,
        idempotency_key=harness.key,
        tasks=dependency(harness),
        enqueuer=dependency(harness),
        files=harness,
    )

    assert result.replayed
    assert result.task.status == "succeeded"
    assert harness.calls == ["reserve", "file", "file"]


async def test_terminal_semantic_replay_provider_failure_is_not_enqueued() -> None:
    harness = SubmissionHarness(
        created=False,
        status="succeeded",
        reauthorize_replay=True,
    )
    harness.output_file_error = DriveUpstreamError(503)

    with pytest.raises(DriveUpstreamError):
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )

    assert harness.calls == ["reserve", "file", "file"]


@pytest.mark.parametrize("status", ["running", "succeeded"])
async def test_in_place_replay_allows_the_transform_own_modified_time(
    status: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_IN_PLACE_ENABLED", True)
    harness = SubmissionHarness(
        created=False,
        status=status,
        reauthorize_replay=True,
        strategy=OutputStrategy.IN_PLACE,
    )
    harness.source_modified_time = "changed-by-canonical-transform"

    result = await submit_document_transform(
        payload=harness.request,
        owner_user_id=harness.owner,
        idempotency_key=harness.key,
        tasks=dependency(harness),
        enqueuer=dependency(harness),
        files=harness,
    )

    assert result.replayed
    expected_calls = ["reserve", "file"]
    if status == "running":
        expected_calls.append("enqueue")
    assert harness.calls == expected_calls


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


async def test_changed_source_revision_is_rejected_before_dispatch() -> None:
    harness = SubmissionHarness()
    harness.source_modified_time = "changed-time"
    with pytest.raises(HTTPException) as caught:
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == "The source changed after the transformation was requested"
    assert caught.value.headers == {"X-Wiki-Error-Code": SOURCE_CHANGED_ERROR_CODE}
    assert harness.calls == ["reserve", "file"]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(FileNotInScopeError("Gone"), id="outside-scope"),
        pytest.param(DriveUpstreamError(404), id="provider-not-found"),
    ],
)
async def test_terminal_copy_replay_treats_definitively_unavailable_output_as_missing(
    error: Exception,
) -> None:
    harness = SubmissionHarness(created=False, status="succeeded")
    harness.output_file_error = error

    assert not await retained_transform_output_available(harness.reservation.task, harness)
    assert harness.calls == ["file"]


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
async def test_terminal_copy_replay_fails_closed_on_provider_or_auth_error(
    status_code: int,
) -> None:
    harness = SubmissionHarness(created=False, status="succeeded")
    harness.output_file_error = DriveUpstreamError(status_code)

    with pytest.raises(DriveUpstreamError):
        await retained_transform_output_available(harness.reservation.task, harness)
    assert harness.calls == ["file"]


async def test_terminal_in_place_replay_relies_on_source_authorization() -> None:
    harness = SubmissionHarness(
        created=False,
        status="succeeded",
        strategy=OutputStrategy.IN_PLACE,
    )

    assert await retained_transform_output_available(harness.reservation.task, harness)
    assert harness.calls == []


async def test_current_polish_reauthorizes_then_matches_live_docs_revision() -> None:
    harness = SubmissionHarness(created=False, status="succeeded", strategy=OutputStrategy.IN_PLACE)
    task = harness.reservation.task
    assert task.status == "succeeded"
    assert isinstance(task.result, DocumentTransformResult)
    harness.current_result = task.result.model_copy(
        update={
            "output": task.result.output.model_copy(update={"file_id": FileId("source")}),
            "output_revision_id": "revision-current",
        }
    )

    current = await get_current_document_polish(
        file_id=FileId("source"),
        mode=TransformMode.FORMAT,
        owner_user_id=harness.owner,
        tasks=dependency(harness),
        files=harness,
        revisions=harness,
    )

    assert current.result is harness.current_result
    assert harness.calls == [
        "file",
        "revision:source",
        f"current:{harness.owner}:source:format:revision-current",
    ]


async def test_current_polish_rejects_non_doc_before_revision_or_task_lookup() -> None:
    harness = SubmissionHarness()
    harness.source_mime_type = "application/pdf"

    with pytest.raises(HTTPException, match="native Google Docs") as caught:
        await get_current_document_polish(
            file_id=FileId("source"),
            mode=TransformMode.FORMAT,
            owner_user_id=harness.owner,
            tasks=dependency(harness),
            files=harness,
            revisions=harness,
        )

    assert caught.value.status_code == 422
    assert harness.calls == ["file"]


async def test_current_polish_fails_closed_when_live_revision_is_unavailable() -> None:
    harness = SubmissionHarness()
    harness.current_revision_error = True

    with pytest.raises(HTTPException, match="could not be verified") as caught:
        await get_current_document_polish(
            file_id=FileId("source"),
            mode=TransformMode.FORMAT,
            owner_user_id=harness.owner,
            tasks=dependency(harness),
            files=harness,
            revisions=harness,
        )

    assert caught.value.status_code == 503
    assert harness.calls == ["file"]


async def test_copy_destination_admits_native_doc_and_writable_folder() -> None:
    harness = SubmissionHarness()
    harness.request = payload().model_copy(
        update={"copy_destination_folder_id": FileId("destination")}
    )

    await submit_document_transform(
        payload=harness.request,
        owner_user_id=harness.owner,
        idempotency_key=harness.key,
        tasks=dependency(harness),
        enqueuer=dependency(harness),
        files=harness,
    )

    assert harness.calls == ["reserve", "file", "file", "enqueue"]


async def test_copy_destination_rejects_non_native_source_before_destination_lookup() -> None:
    harness = SubmissionHarness()
    harness.source_mime_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    harness.request = payload().model_copy(
        update={"copy_destination_folder_id": FileId("destination")}
    )

    with pytest.raises(HTTPException, match="native Google Docs") as caught:
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )

    assert caught.value.status_code == 422
    assert harness.calls == ["reserve", "file"]


async def test_copy_destination_rejects_unwritable_folder() -> None:
    harness = SubmissionHarness()
    harness.destination_can_add_children = False
    harness.request = payload().model_copy(
        update={"copy_destination_folder_id": FileId("destination")}
    )

    with pytest.raises(FileNotInScopeError, match="Destination"):
        await submit_document_transform(
            payload=harness.request,
            owner_user_id=harness.owner,
            idempotency_key=harness.key,
            tasks=dependency(harness),
            enqueuer=dependency(harness),
            files=harness,
        )

    assert harness.calls == ["reserve", "file", "file"]
