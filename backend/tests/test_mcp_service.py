from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, override
import uuid

import pytest

from app.drive.schemas import SearchResult as DriveSearchResult
from app.drive.service import BoundedSearchResult
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import FileId, IdempotencyKey, TaskId, UserId
from app.mcp.context import McpAuthorizationError, ValidatedMcpPrincipal
from app.mcp.schemas import (
    AdmissionInput,
    DocumentInput,
    McpCapability,
    SearchInput,
    ShortLinkInput,
    TaskInput,
)
from app.mcp.service import McpReadService
from app.tasks.errors import TaskNotFoundError
from app.tasks.schemas import FailedTaskRead, TaskKind, TaskStatus, UnverifiedTaskOutput


class Services:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.exports = 0
        self.owners: list[uuid.UUID] = []

    async def get(self, file_id: FileId) -> FileResponse:
        if not self.allowed:
            message = "Not available"
            raise FileNotInScopeError(message)
        return FileResponse(
            id=file_id,
            name="Private Policy",
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=None,
            drive_id=None,
            modified_time=None,
        )

    async def fetch_document_text(self, file_id: FileId) -> str:
        self.exports += 1
        return "x" * 15000

    @asynccontextmanager
    async def search_bounded(
        self, query: str, *, limit: int
    ) -> AsyncGenerator[BoundedSearchResult]:
        yield BoundedSearchResult(
            [
                DriveSearchResult(
                    id=FileId("private-file"),
                    name="Private Policy",
                    mime_type="text/plain",
                    drive_id=None,
                    drive_name=None,
                    parent_id=None,
                    modified_time=None,
                )
            ],
            True,
        )

    async def get_accessible_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        return {file_id: await self.get(file_id) for file_id in file_ids} if self.allowed else {}

    async def get_search_metadata_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        return await self.get_accessible_many(file_ids)

    async def get_for_owner(self, task_id: TaskId, owner: uuid.UUID) -> Any:
        self.owners.append(owner)
        raise TaskNotFoundError

    async def get_document_transform_admission(self, owner: uuid.UUID, key: IdempotencyKey) -> Any:
        self.owners.append(owner)
        raise TaskNotFoundError


def make_context(services: Services, *, read: bool = True) -> Any:
    return SimpleNamespace(
        principal=ValidatedMcpPrincipal(
            UserId(uuid.uuid7()), 1, frozenset({McpCapability.READ}) if read else frozenset()
        ),
        drive=services,
        files=services,
        tasks=services,
        aliases=services,
    )


async def test_two_user_document_scope_precedes_excerpt() -> None:
    allowed, denied = Services(), Services(allowed=False)
    request = DocumentInput(file_id=FileId("private-file"), include_excerpt=True)
    result = await McpReadService(make_context(allowed)).get_document(request)
    assert result.excerpt is not None
    assert len(result.excerpt.text) == 12000
    assert result.excerpt.complete is False
    with pytest.raises(FileNotInScopeError):
        await McpReadService(make_context(denied)).get_document(request)
    assert denied.exports == 0


async def test_excerpt_opt_in() -> None:
    services = Services()
    result = await McpReadService(make_context(services)).get_document(
        DocumentInput(file_id=FileId("file"))
    )
    assert result.excerpt is None
    assert services.exports == 0


async def test_search_omits_denied_metadata() -> None:
    result = await McpReadService(make_context(Services(allowed=False))).search(
        SearchInput(query="policy")
    )
    assert result.results == []
    assert result.truncated is True
    assert result.complete is False


async def test_two_user_task_and_admission_reads_bind_authenticated_owner() -> None:
    services = Services()
    first, second = make_context(services), make_context(services)
    task_id, key = TaskId(uuid.uuid7()), IdempotencyKey(uuid.uuid7())
    for context in (first, second):
        service = McpReadService(context)
        with pytest.raises(TaskNotFoundError):
            await service.get_task(TaskInput(task_id=task_id))
        with pytest.raises(TaskNotFoundError):
            await service.get_admission(AdmissionInput(idempotency_key=key))
    assert services.owners == [first.principal.user_id] * 2 + [second.principal.user_id] * 2
    assert first.principal.user_id != second.principal.user_id


async def test_scope_denial_precedes_content_calls() -> None:
    services = Services()
    service = McpReadService(make_context(services, read=False))
    with pytest.raises(McpAuthorizationError):
        service.connection()
    with pytest.raises(McpAuthorizationError):
        await service.get_document(DocumentInput(file_id=FileId("file"), include_excerpt=True))
    assert services.exports == 0


@pytest.mark.parametrize("kind", list(TaskKind))
async def test_failed_task_hides_unverified_output_and_internal_detail(kind: TaskKind) -> None:
    now = datetime.now(UTC)
    task = FailedTaskRead(
        id=TaskId(uuid.uuid7()),
        kind=kind,
        created_at=now,
        status=TaskStatus.FAILED,
        result=None,
        resource_id=FileId("hidden-output"),
        error_code="provider_error",
        error_detail="internal provider body",
        unverified_output=UnverifiedTaskOutput(file_id=FileId("hidden-output")),
        attempt_count=1,
        started_at=now,
        finished_at=now,
    )

    class FailedServices(Services):
        @override
        async def get_for_owner(self, task_id: TaskId, owner: uuid.UUID) -> Any:
            return task

    service = McpReadService(make_context(FailedServices()))
    if kind is TaskKind.FILE_SMART_CREATE:
        with pytest.raises(TaskNotFoundError):
            await service.get_task(TaskInput(task_id=task.id))
        return
    response = await service.get_task(TaskInput(task_id=task.id))
    assert "hidden-output" not in response.model_dump_json()
    assert "internal provider body" not in response.model_dump_json()
    assert isinstance(response.task, FailedTaskRead)
    assert response.task.unverified_output is None


async def test_alias_uses_acl_checked_service_without_mutation() -> None:
    class AliasServices(Services):
        async def get_for_file(self, file_id: FileId) -> Any:
            await self.get(file_id)
            return SimpleNamespace(alias=None)

    request = ShortLinkInput(file_id=FileId("private-file"))
    result = await McpReadService(make_context(AliasServices())).get_short_link(request)
    assert result.alias is None
    assert result.wiki_url is None
    with pytest.raises(FileNotInScopeError):
        await McpReadService(make_context(AliasServices(allowed=False))).get_short_link(request)
