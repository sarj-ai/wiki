from dataclasses import replace
from typing import Literal, assert_never
import uuid

from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import auth_service_dep, get_current_user
from app.document_transform.dependencies import CurrentPolishRuntime, current_polish_runtime_dep
from app.document_transform.errors import SourceChangedError
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.inspection import attach_saved_checkpoint, inspect_native_document
from app.document_transform.schemas import DriveFileSource
from app.identifiers import DocsRevisionId, FileId, TaskId, UserId
from app.main import app
from app.tasks.dependencies import task_service_dep
from app.tasks.errors import TaskNotFoundError
from app.tasks.schemas import (
    DocumentTransformTaskRead,
    DocumentTransformTaskRecord,
    FailedTaskRead,
    NeedsReviewTaskRead,
    ReviewDocumentTransformTaskRecord,
    TaskKind,
    TaskStatus,
    TerminalDocumentTransformTaskRecord,
)
from tests.advanced_round6_support import in_place_checkpoint
from tests.support import dependency
from tests.test_document_readability import _fixture
from tests.test_files_service import _user
from tests.test_task_unverified_output import _failed_task


class _Reader:
    def __init__(self) -> None:
        self.calls: list[DriveFileSource] = []
        self.snapshot = SourceSnapshot(
            title="Private native fixture",
            parent_folder_id=FileId("private-folder"),
            modified_time="2026-11-12T13:00:00Z",
            revision_id=DocsRevisionId("native-revision"),
            media_type="application/vnd.google-apps.document",
            google_document={**_fixture("before"), "revisionId": "native-revision"},
        )

    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot:
        self.calls.append(source)
        return self.snapshot


def _source() -> DriveFileSource:
    return DriveFileSource(
        kind="drive_file", file_id=FileId("private"), expected_modified_time="2026-11-12T13:00:00Z"
    )


async def test_native_inspection_preserves_provider_payload_and_binds_requested_revision() -> None:
    reader = _Reader()
    result = await inspect_native_document(_source(), reader=reader, account_email="owner@sarj.ai")
    assert result.account_email == "owner@sarj.ai"
    assert result.revision_id == "native-revision"
    assert result.document == reader.snapshot.google_document
    reader.snapshot = replace(reader.snapshot, modified_time="2026-11-12T14:00:00Z")
    with pytest.raises(SourceChangedError):
        await inspect_native_document(_source(), reader=reader, account_email="owner@sarj.ai")


async def test_native_inspection_requires_wiki_auth_and_disables_response_caching() -> None:
    reader = _Reader()
    runtime = CurrentPolishRuntime(
        files=dependency(reader), revisions=dependency(reader), native_sources=reader
    )
    app.dependency_overrides[current_polish_runtime_dep] = lambda: runtime
    app.dependency_overrides[auth_service_dep] = lambda: None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.post("/document-transforms/inspect", json=_source().model_dump())
            assert denied.status_code == 401
            assert not reader.calls
            app.dependency_overrides[get_current_user] = _user
            response = await client.post(
                "/document-transforms/inspect", json=_source().model_dump()
            )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["document"] == reader.snapshot.google_document
        assert reader.calls == [_source()]
    finally:
        app.dependency_overrides.pop(current_polish_runtime_dep, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(auth_service_dep, None)


class _SavedTaskReader:
    def __init__(
        self,
        *,
        hidden: bool = False,
        wrong_owner: bool = False,
        wrong_file: bool = False,
        review: bool = False,
    ) -> None:
        self.user = _user()
        terminal = TerminalDocumentTransformTaskRecord(
            owner_user_id=uuid.uuid7() if wrong_owner else self.user.id,
            task=_failed_task(resource_id=FileId("another-file" if wrong_file else "private")),
            payload=None,
            checkpoint=in_place_checkpoint(),
            checkpoint_generation=3,
        )
        self.record: DocumentTransformTaskRecord
        if review:
            self.record = ReviewDocumentTransformTaskRecord(
                owner_user_id=terminal.owner_user_id,
                task=NeedsReviewTaskRead[Literal[TaskKind.FILE_DOCUMENT_TRANSFORM]].model_validate(
                    terminal.task.model_dump()
                    | {
                        "status": TaskStatus.NEEDS_REVIEW,
                        "error_code": "external_effect_uncertain",
                        "error_detail": "The provider action needs review",
                    }
                ),
            )
        else:
            self.record = terminal
        self.hidden = hidden
        self.loads: list[TaskId] = []

    async def get_for_owner(
        self, task_id: TaskId, owner_user_id: UserId
    ) -> DocumentTransformTaskRead:
        if self.hidden or owner_user_id != self.record.owner_user_id:
            raise TaskNotFoundError
        assert task_id == self.record.task.id
        assert isinstance(self.record.task, FailedTaskRead | NeedsReviewTaskRead)
        return self.record.task

    async def load_document_transform(self, task_id: TaskId) -> DocumentTransformTaskRecord:
        self.loads.append(task_id)
        assert task_id == self.record.task.id
        return self.record


@pytest.mark.parametrize("mismatch", ["owner", "file"])
async def test_saved_checkpoint_cannot_be_attached_to_another_owner_or_file(mismatch: str) -> None:
    inspection = await inspect_native_document(
        _source(), reader=_Reader(), account_email="owner@sarj.ai"
    )
    tasks = _SavedTaskReader(wrong_owner=mismatch == "owner", wrong_file=mismatch == "file")
    with pytest.raises(TaskNotFoundError):
        attach_saved_checkpoint(inspection, tasks.record, owner_user_id=UserId(tasks.user.id))


@pytest.mark.parametrize(
    "scenario", ["visible", "hidden_task", "wrong_file", "changed_source", "review_task"]
)
async def test_saved_checkpoint_requires_owner_and_fresh_native_document_access(
    scenario: Literal["visible", "hidden_task", "wrong_file", "changed_source", "review_task"],
) -> None:
    reader = _Reader()
    if scenario == "changed_source":
        reader.snapshot = replace(reader.snapshot, modified_time="2026-11-12T14:00:00Z")
    tasks = _SavedTaskReader(
        hidden=scenario == "hidden_task",
        wrong_file=scenario == "wrong_file",
        review=scenario == "review_task",
    )
    runtime = CurrentPolishRuntime(
        files=dependency(reader), revisions=dependency(reader), native_sources=reader
    )
    app.dependency_overrides[current_polish_runtime_dep] = lambda: runtime
    app.dependency_overrides[get_current_user] = lambda: tasks.user
    app.dependency_overrides[task_service_dep] = lambda: tasks
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/document-transforms/inspect",
                params={"task_id": str(tasks.record.task.id)},
                json=_source().model_dump(),
            )
        match scenario:
            case "visible":
                assert response.status_code == 200
                assert response.headers["cache-control"] == "private, no-store"
                assert response.json()["kind"] == "saved-task"
                assert isinstance(tasks.record, TerminalDocumentTransformTaskRecord)
                assert tasks.record.checkpoint is not None
                assert response.json()["checkpoint"] == tasks.record.checkpoint.model_dump(
                    mode="json"
                )
                assert response.json()["checkpoint_generation"] == 3
            case "review_task":
                assert response.status_code == 409
                assert response.json()["code"] == "task_not_claimable"
                assert "checkpoint" not in response.json()
            case "changed_source":
                assert response.status_code == 409
                assert "checkpoint" not in response.json()
            case "hidden_task" | "wrong_file":
                assert response.status_code == 404
                assert "checkpoint" not in response.json()
            case _:
                assert_never(scenario)
        assert tasks.loads == (
            [tasks.record.task.id] if scenario in {"visible", "wrong_file", "review_task"} else []
        )
    finally:
        app.dependency_overrides.pop(current_polish_runtime_dep, None)
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(task_service_dep, None)
