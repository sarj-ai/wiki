from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import json
from typing import Never, override
import uuid

from cryptography.fernet import InvalidToken
import httpx
import pytest

from app.aliases.schemas import DocumentAliasRead
from app.config import settings
from app.document_transform import task_executor as task_executor_module
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import (
    DocumentGatewayError,
    OutputVerificationError,
    UnsupportedSourceError,
)
from app.document_transform.executor import PIPELINE_VERSION
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    MarkdownSource,
    OutputStrategy,
    TransformFinding,
    TransformMode,
    ValidationSummary,
)
from app.document_transform.task_executor import (
    DocumentTransformTaskExecutor,
    DriveDocumentTransformTaskExecutor,
    defer_or_fail_retryable_task,
)
from app.drive.retry import ProviderDeadlineError
from app.identifiers import FileId, TaskId
from app.performance import (
    begin_request_metrics,
    provider_retry_remaining_seconds,
    reset_request_metrics,
)
from app.tasks import service as task_service_module
from app.tasks.errors import TaskExecutionDeferredError
from app.tasks.internal_router import execute_document_transform_task
from app.tasks.schemas import (
    DocumentTransformTaskRecord,
    FailedTaskRead,
    RunningTaskRead,
    SucceededTaskRead,
    TaskClaim,
    TaskDispatchRequest,
    TaskKind,
    TaskRead,
    TaskStatus,
)


class _RetryTasks:
    def __init__(
        self,
        *,
        attempt_count: int = 1,
        payload: DocumentTransformRequest | None = None,
    ) -> None:
        self.deferred = 0
        self.failed = 0
        self.attempt_count = attempt_count
        self.payload = payload or _payload()
        self.last_error_code: str | None = None

    async def defer(self, task_id: TaskId, *, lease_token: uuid.UUID) -> TaskRead:
        self.deferred += 1
        return _claim(task_id, attempt_count=1).task

    async def fail(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        error_code: str,
        error_detail: str | None,
    ) -> TaskRead:
        self.failed += 1
        self.last_error_code = error_code
        now = datetime.now(UTC)
        return FailedTaskRead(
            id=task_id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            status=TaskStatus.FAILED,
            result=None,
            resource_id=None,
            error_code=error_code,
            error_detail=error_detail,
            attempt_count=10,
            created_at=now,
            started_at=now,
            finished_at=now,
        )

    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None:
        del task_id, lease_token, resource_id
        pytest.fail("deadline path must not record a resource")

    async def succeed(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        result: object,
    ) -> TaskRead:
        del task_id, lease_token, result
        pytest.fail("deadline path must not succeed")

    async def load_document_transform(
        self,
        task_id: TaskId,
    ) -> DocumentTransformTaskRecord:
        return DocumentTransformTaskRecord(
            owner_user_id=uuid.uuid7(),
            task=_claim(task_id, attempt_count=self.attempt_count).task,
            payload=self.payload,
        )

    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        del lease_duration
        return _claim(task_id, attempt_count=self.attempt_count)


class _UnreadableTask(_RetryTasks):
    def __init__(self, failure: Exception | None) -> None:
        super().__init__()
        self.failure = failure
        self.claimed = 0

    @override
    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord:
        if self.failure is not None:
            raise self.failure
        return DocumentTransformTaskRecord(
            owner_user_id=uuid.uuid7(),
            task=_claim(task_id, attempt_count=1).task,
            payload=None,
        )

    @override
    async def claim(self, task_id: TaskId, *, lease_duration: timedelta) -> TaskClaim:
        self.claimed += 1
        return await super().claim(task_id, lease_duration=lease_duration)


class _DeadlineTransform:
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> Never:
        del operation_id, request
        raise ProviderDeadlineError


class _NonRetryableGatewayTransform:
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> Never:
        del operation_id, request
        raise DocumentGatewayError(retryable=False, http_status=400)


class _UnexpectedCrashTransform:
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> Never:
        del operation_id, request
        msg = "private document content must not escape"
        raise RuntimeError(msg)


class _VerificationFailureTransform:
    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> Never:
        del operation_id, request
        raise OutputVerificationError


class _SuccessTransform:
    def __init__(self) -> None:
        self.markdown: str | None = None
        self.needs_review = False
        self.review_code = "native_date_provider_rejected"
        self.protected_content_preserved = True
        self.structure_preserved = True
        self.source_unchanged = True
        self.calls = 0

    async def execute(
        self,
        *,
        operation_id: uuid.UUID,
        request: DocumentTransformRequest,
    ) -> DocumentTransformResult:
        self.calls += 1
        if isinstance(request.source, MarkdownSource):
            self.markdown = request.source.content
        return DocumentTransformResult(
            operation_id=operation_id,
            source_sha256="source",
            output=DocumentOutput(
                file_id=request.source.file_id
                if request.output_strategy is OutputStrategy.IN_PLACE
                and isinstance(request.source, DriveFileSource)
                else FileId("output"),
                name="Output",
                parent_folder_id=FileId("folder"),
            ),
            mode=request.mode,
            applied_patch_count=0,
            findings=[TransformFinding(code=self.review_code, detail="Original content retained")]
            if self.needs_review
            else [],
            validation=ValidationSummary(
                source_unchanged=self.source_unchanged,
                protected_content_preserved=self.protected_content_preserved,
                structure_preserved=self.structure_preserved,
                unsafe_patch_count=0,
            ),
            model_ids=[],
            tool_versions={},
            pipeline_version=PIPELINE_VERSION,
            needs_review=self.needs_review,
            output_strategy=request.output_strategy,
            brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
            brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
            deterministic_patch_count=0,
            model_patch_count=0,
            llm_invoked=False,
        )


class _SuccessTasks(_RetryTasks):
    def __init__(self, *, payload: DocumentTransformRequest) -> None:
        super().__init__(payload=payload)
        self.recorded_resource: FileId | None = None

    @override
    async def record_resource(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        resource_id: FileId,
    ) -> None:
        del task_id, lease_token
        self.recorded_resource = resource_id

    @override
    async def succeed(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        result: object,
    ) -> TaskRead:
        del lease_token
        assert isinstance(result, DocumentTransformResult)
        now = datetime.now(UTC)
        return SucceededTaskRead(
            id=task_id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            status=TaskStatus.SUCCEEDED,
            result=result,
            resource_id=self.recorded_resource,
            error_code=None,
            error_detail=None,
            attempt_count=1,
            created_at=now,
            started_at=now,
            finished_at=now,
        )


class _ShortLinks:
    def __init__(self, alias: str | None) -> None:
        self.alias = alias
        self.calls: list[tuple[FileId, str]] = []

    async def ensure(self, output_id: FileId, title: str) -> str | None:
        self.calls.append((output_id, title))
        return self.alias


class _AutomaticAliases:
    def __init__(self, *, fails: bool) -> None:
        self.fails = fails
        self.file_id: FileId | None = None

    async def ensure_for_file(
        self, file_id: FileId, candidates: tuple[str, ...]
    ) -> DocumentAliasRead:
        self.file_id = file_id
        assert candidates
        if self.fails:
            msg = "private provider detail"
            raise RuntimeError(msg)
        return DocumentAliasRead(alias="output-document", file_id=file_id)


class _AliasTransaction:
    def __init__(self) -> None:
        self.rollbacks = 0

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.parametrize("fails", [False, True])
async def test_automatic_short_links_roll_back_only_failed_link_attempt(fails: bool) -> None:
    aliases = _AutomaticAliases(fails=fails)
    db = _AliasTransaction()
    links = task_executor_module._AutomaticShortLinks(aliases=aliases, db=db)
    alias = await links.ensure(FileId("output"), "Output")
    assert alias == (None if fails else "output-document")
    assert db.rollbacks == int(fails)
    assert aliases.file_id == FileId("output")


@pytest.mark.parametrize("alias", ["output-document", None])
async def test_success_records_automatic_short_link_without_replaying_transform(
    alias: str | None,
) -> None:
    payload = _payload()
    task_id = TaskId(uuid.uuid7())
    links = _ShortLinks(alias)
    tasks = _SuccessTasks(payload=payload)
    completed = await task_executor_module._execute_claimed(
        task_id,
        tasks=tasks,
        transform=_SuccessTransform(),
        claim=_claim(task_id, attempt_count=1),
        payload=payload,
        short_links=links,
    )
    assert isinstance(completed, SucceededTaskRead)
    assert isinstance(completed.result, DocumentTransformResult)
    assert completed.result.short_link_alias == alias
    assert completed.result.short_link_status == ("ready" if alias else "unavailable")
    assert links.calls == [(FileId("output"), "Output")]
    assert tasks.failed == tasks.deferred == 0


@pytest.mark.parametrize("alias", ["output-document", None])
@pytest.mark.parametrize(
    "review_code", ["native_date_provider_rejected", "google_doc_text_target_unsupported"]
)
async def test_preserved_review_output_gets_automatic_link_without_approving_findings(
    alias: str | None,
    review_code: str,
) -> None:
    payload = _payload()
    task_id = TaskId(uuid.uuid7())
    links = _ShortLinks(alias)
    transform = _SuccessTransform()
    transform.needs_review = True
    transform.review_code = review_code
    completed = await task_executor_module._execute_claimed(
        task_id,
        tasks=_SuccessTasks(payload=payload),
        transform=transform,
        claim=_claim(task_id, attempt_count=1),
        payload=payload,
        short_links=links,
    )
    assert isinstance(completed, SucceededTaskRead)
    assert isinstance(completed.result, DocumentTransformResult)
    assert completed.result.short_link_status == ("ready" if alias else "unavailable")
    assert completed.result.short_link_alias == alias
    assert completed.result.needs_review is True
    assert completed.result.findings[0].code == review_code
    assert links.calls == [(FileId("output"), "Output")]
    assert transform.calls == 1


@pytest.mark.parametrize(("protected", "structure"), [(False, True), (True, False), (False, False)])
async def test_unpreserved_review_output_never_gets_automatic_link(
    protected: bool, structure: bool
) -> None:
    payload = _payload()
    task_id = TaskId(uuid.uuid7())
    transform = _SuccessTransform()
    transform.needs_review = True
    transform.protected_content_preserved = protected
    transform.structure_preserved = structure
    links = _ShortLinks("output-document")
    completed = await task_executor_module._execute_claimed(
        task_id,
        tasks=_SuccessTasks(payload=payload),
        transform=transform,
        claim=_claim(task_id, attempt_count=1),
        payload=payload,
        short_links=links,
    )
    assert isinstance(completed, SucceededTaskRead)
    assert isinstance(completed.result, DocumentTransformResult)
    assert completed.result.short_link_status is None
    assert completed.result.needs_review is True
    assert links.calls == []


async def test_successful_in_place_output_links_despite_expected_source_change() -> None:
    payload = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-04T00:00:00Z",
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )
    task_id = TaskId(uuid.uuid7())
    transform = _SuccessTransform()
    transform.source_unchanged = False
    links = _ShortLinks("output-document")
    completed = await task_executor_module._execute_claimed(
        task_id,
        tasks=_SuccessTasks(payload=payload),
        transform=transform,
        claim=_claim(task_id, attempt_count=1),
        payload=payload,
        short_links=links,
    )
    assert isinstance(completed, SucceededTaskRead)
    assert isinstance(completed.result, DocumentTransformResult)
    assert completed.result.validation.source_unchanged is False
    assert completed.result.short_link_alias == "output-document"
    assert links.calls == [(FileId("source"), "Output")]


async def test_verification_exception_never_attempts_alias() -> None:
    payload = _payload()
    task_id = TaskId(uuid.uuid7())
    links = _ShortLinks("output-document")
    await task_executor_module._execute_claimed(
        task_id,
        tasks=_SuccessTasks(payload=payload),
        transform=_VerificationFailureTransform(),
        claim=_claim(task_id, attempt_count=1),
        payload=payload,
        short_links=links,
    )
    assert links.calls == []


class _BudgetProbeExecutor(DocumentTransformTaskExecutor):
    def __init__(self) -> None:
        self.remaining_seconds: float | None = None

    @override
    async def execute(self, task_id: TaskId) -> TaskRead:
        self.remaining_seconds = provider_retry_remaining_seconds()
        return _claim(task_id, attempt_count=1).task


class _UnreadableLogoStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield pytest.fail("oversized declared logo must not be read")


async def test_logo_stream_rejects_oversized_content_length_before_read() -> None:
    response = httpx.Response(
        200,
        headers={"Content-Length": str(task_executor_module._MAX_LOGO_BYTES + 1)},
        stream=_UnreadableLogoStream(),
        request=httpx.Request("GET", CURRENT_BRAND_MANIFEST.assets.logo_uri),
    )

    assert await task_executor_module._bounded_logo_content(response) is None


async def test_retryable_failure_defers_before_attempt_limit() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())

    with pytest.raises(TaskExecutionDeferredError):
        await defer_or_fail_retryable_task(
            task_id,
            tasks=tasks,
            claim=_claim(task_id, attempt_count=9),
            error_code="model_unavailable",
            error_detail="Retry later",
            cause=RuntimeError("provider timeout"),
        )

    assert tasks.deferred == 1
    assert tasks.failed == 0


async def test_retryable_failure_becomes_terminal_at_attempt_limit() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())

    result = await defer_or_fail_retryable_task(
        task_id,
        tasks=tasks,
        claim=_claim(task_id, attempt_count=10),
        error_code="model_unavailable",
        error_detail="Retry later",
        cause=RuntimeError("provider timeout"),
    )

    assert result.status is TaskStatus.FAILED
    assert tasks.deferred == 0
    assert tasks.failed == 1


async def test_provider_deadline_defers_claimed_task_without_stranding_lease() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())

    with pytest.raises(TaskExecutionDeferredError):
        await task_executor_module._execute_claimed(
            task_id,
            tasks=tasks,
            transform=_DeadlineTransform(),
            claim=_claim(task_id, attempt_count=1),
            payload=tasks.payload,
        )

    assert tasks.deferred == 1
    assert tasks.failed == 0


async def test_provider_deadline_becomes_terminal_at_attempt_limit() -> None:
    tasks = _RetryTasks(attempt_count=10)
    task_id = TaskId(uuid.uuid7())

    result = await task_executor_module._execute_claimed(
        task_id,
        tasks=tasks,
        transform=_DeadlineTransform(),
        claim=_claim(task_id, attempt_count=10),
        payload=tasks.payload,
    )

    assert isinstance(result, FailedTaskRead)
    assert result.error_code == "document_provider_unavailable"
    assert tasks.deferred == 0
    assert tasks.failed == 1


async def test_nonretryable_provider_error_fails_immediately() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())

    result = await task_executor_module._execute_claimed(
        task_id,
        tasks=tasks,
        transform=_NonRetryableGatewayTransform(),
        claim=_claim(task_id, attempt_count=1),
        payload=tasks.payload,
    )

    assert isinstance(result, FailedTaskRead)
    assert result.error_code == "document_provider_unavailable"
    assert tasks.deferred == 0
    assert tasks.failed == 1


async def test_unexpected_worker_crash_is_bounded_and_sanitized() -> None:
    tasks = _RetryTasks(attempt_count=10)
    task_id = TaskId(uuid.uuid7())

    result = await task_executor_module._execute_claimed(
        task_id,
        tasks=tasks,
        transform=_UnexpectedCrashTransform(),
        claim=_claim(task_id, attempt_count=10),
        payload=tasks.payload,
    )

    assert isinstance(result, FailedTaskRead)
    assert result.error_code == "document_transform_internal_error"
    assert result.error_detail == "The document transformation could not be completed"
    assert "private document content" not in (result.error_detail or "")
    assert tasks.deferred == 0
    assert tasks.failed == 1


async def test_output_verification_failure_is_terminal_not_stale_success() -> None:
    tasks = _RetryTasks()
    task_id = TaskId(uuid.uuid7())

    result = await task_executor_module._execute_claimed(
        task_id,
        tasks=tasks,
        transform=_VerificationFailureTransform(),
        claim=_claim(task_id, attempt_count=1),
        payload=tasks.payload,
    )

    assert isinstance(result, FailedTaskRead)
    assert result.error_code == "output_verification_failed"
    assert tasks.deferred == 0
    assert tasks.failed == 1


async def test_encrypted_markdown_is_decrypted_only_for_successful_worker_execution() -> None:
    payload = _payload()
    assert isinstance(payload.source, MarkdownSource)
    serialized = task_service_module._serialize(payload)
    persisted = task_service_module._payload_for_storage(
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        payload=payload,
        serialized=serialized,
    )
    assert payload.source.content not in json.dumps(persisted, sort_keys=True)
    decrypted = task_service_module._deserialize_document_transform_payload(persisted)
    tasks = _SuccessTasks(payload=decrypted)
    transform = _SuccessTransform()
    task_id = TaskId(uuid.uuid7())

    result = await task_executor_module._execute_claimed(
        task_id,
        tasks=tasks,
        transform=transform,
        claim=_claim(task_id, attempt_count=1),
        payload=decrypted,
    )

    assert isinstance(result, SucceededTaskRead)
    assert transform.markdown == payload.source.content
    assert tasks.recorded_resource == FileId("output")


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(ValueError("private Markdown content"), id="invalid-value"),
        pytest.param(TypeError("bad checkpoint"), id="invalid-type"),
        pytest.param(InvalidToken(), id="invalid-token"),
        pytest.param(None, id="missing-payload"),
    ],
)
async def test_unreadable_saved_request_fails_after_claim_without_provider_call(
    monkeypatch: pytest.MonkeyPatch, failure: Exception | None
) -> None:
    tasks = _UnreadableTask(failure)
    executor = object.__new__(DriveDocumentTransformTaskExecutor)
    monkeypatch.setattr(executor, "_tasks", tasks, raising=False)

    async def forbidden_runtime(*args: object, **kwargs: object) -> Never:
        del args, kwargs
        pytest.fail("unreadable request must not initialize the provider")

    monkeypatch.setattr(DriveDocumentTransformTaskExecutor, "_runtime", forbidden_runtime)
    result = await executor.execute(TaskId(uuid.uuid7()))

    assert isinstance(result, FailedTaskRead)
    assert tasks.claimed == 1
    assert tasks.failed == 1
    assert result.error_code == "invalid_task_payload"
    assert result.error_detail == "The saved document transformation request could not be read"


async def test_internal_document_transform_gets_worker_provider_budget() -> None:
    task_id = TaskId(uuid.uuid7())
    executor = _BudgetProbeExecutor()
    tokens = begin_request_metrics()
    try:
        interactive_budget = provider_retry_remaining_seconds()
        await execute_document_transform_task(
            TaskDispatchRequest(task_id=task_id),
            executor,
        )
        restored_budget = provider_retry_remaining_seconds()
    finally:
        reset_request_metrics(tokens)

    assert interactive_budget is not None
    assert executor.remaining_seconds is not None
    assert executor.remaining_seconds > 230
    assert restored_budget is not None
    assert restored_budget <= interactive_budget


@pytest.mark.parametrize("in_place", [False, True])
async def test_crashed_worker_retry_cap_precedes_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    in_place: bool,
) -> None:
    tasks = _RetryTasks(
        attempt_count=11,
        payload=_in_place_payload() if in_place else _drive_payload(),
    )
    executor = object.__new__(DriveDocumentTransformTaskExecutor)
    monkeypatch.setattr(executor, "_tasks", tasks, raising=False)

    async def forbidden_runtime(*args: object, **kwargs: object) -> Never:
        del args, kwargs
        pytest.fail("exhausted claims must not initialize provider or model runtime")

    monkeypatch.setattr(DriveDocumentTransformTaskExecutor, "_runtime", forbidden_runtime)
    result = await executor.execute(TaskId(uuid.uuid7()))

    assert isinstance(result, FailedTaskRead)
    assert tasks.last_error_code == ("needs_review" if in_place else "execution_attempts_exhausted")
    assert tasks.failed == 1


async def test_worker_kill_switch_blocks_claimed_in_place_task_before_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_IN_PLACE_ENABLED", False)
    tasks = _RetryTasks(payload=_in_place_payload())
    executor = object.__new__(DriveDocumentTransformTaskExecutor)
    monkeypatch.setattr(executor, "_tasks", tasks, raising=False)

    result = await executor.execute(TaskId(uuid.uuid7()))

    assert isinstance(result, FailedTaskRead)
    assert tasks.last_error_code == "in_place_disabled"
    assert tasks.failed == 1


async def test_worker_fails_unsupported_drive_source_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = _RetryTasks(payload=_drive_payload())
    executor = object.__new__(DriveDocumentTransformTaskExecutor)
    monkeypatch.setattr(executor, "_tasks", tasks, raising=False)

    async def unsupported_runtime(*args: object, **kwargs: object) -> Never:
        del args, kwargs
        raise UnsupportedSourceError

    monkeypatch.setattr(DriveDocumentTransformTaskExecutor, "_runtime", unsupported_runtime)

    result = await executor.execute(TaskId(uuid.uuid7()))

    assert isinstance(result, FailedTaskRead)
    assert tasks.last_error_code == UnsupportedSourceError.code
    assert tasks.failed == 1


async def test_worker_defers_transient_runtime_drive_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks = _RetryTasks(payload=_drive_payload())
    executor = object.__new__(DriveDocumentTransformTaskExecutor)
    monkeypatch.setattr(executor, "_tasks", tasks, raising=False)

    async def unavailable_runtime(*args: object, **kwargs: object) -> Never:
        del args, kwargs
        raise DocumentGatewayError

    monkeypatch.setattr(DriveDocumentTransformTaskExecutor, "_runtime", unavailable_runtime)

    with pytest.raises(TaskExecutionDeferredError):
        await executor.execute(TaskId(uuid.uuid7()))

    assert tasks.deferred == 1
    assert tasks.failed == 0


def _claim(task_id: TaskId, *, attempt_count: int) -> TaskClaim:
    now = datetime.now(UTC)
    return TaskClaim(
        task=RunningTaskRead(
            id=task_id,
            kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
            status=TaskStatus.RUNNING,
            result=None,
            resource_id=None,
            error_code=None,
            error_detail=None,
            attempt_count=attempt_count,
            created_at=now,
            started_at=now,
            finished_at=None,
        ),
        lease_token=uuid.uuid7(),
    )


def _payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# Decision\n\nThe team approved option B.",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.BOTH,
    )


def _in_place_payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=TransformMode.FORMAT,
        output_strategy=OutputStrategy.IN_PLACE,
    )


def _drive_payload() -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file",
            file_id=FileId("source"),
            expected_modified_time="2026-09-03T00:00:00Z",
        ),
        mode=TransformMode.FORMAT,
    )
