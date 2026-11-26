from collections.abc import Awaitable, Callable
from typing import override
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
import pytest

from app.document_transform.application import TransformSubmission
from app.document_transform.by_id import submit_document_by_id
from app.document_transform.schemas import (
    DocumentIdPolishRequest,
    DocumentTransformRequest,
    OutputStrategy,
    TransformMode,
)
from app.drive.google_schemas import GoogleFileAccessLocation
from app.drive.schemas import MIME_FOLDER, DriveSummary
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, IdempotencyKey, UserId
from app.mcp.context import McpApplicationContext, McpAuthorizationError, ValidatedMcpPrincipal
from app.mcp.mutation import polish_document_by_id
from app.mcp.schemas import McpCapability
from app.tasks.schemas import TaskKind, TaskRead, TaskReservation
from tests.support import dependency
from tests.test_document_transform_application import SubmissionHarness
from tests.test_files_service import _FakeDriveService, _user


class ByIdHarness(SubmissionHarness):
    def __init__(self) -> None:
        super().__init__(reauthorize_replay=True)
        self.source_modified_time = "2026-11-26T12:00:00Z"
        self.direct_reads: list[FileId] = []
        self.member_reads: list[FileId] = []
        self.personal_drive = True
        self.outside_workspace = True
        self.source_parent: FileId | None = FileId("unshared-parent")
        self.source_id = FileId("source")
        self.submissions = 0

    def source(self) -> FileResponse:
        return FileResponse(
            id=self.source_id,
            name="Source",
            mime_type=self.source_mime_type,
            parent_folder_id=self.source_parent,
            drive_id=None,
            modified_time=self.source_modified_time,
        )

    async def get_direct(self, file_id: FileId) -> FileResponse:
        self.direct_reads.append(file_id)
        if self.file_error is not None:
            raise self.file_error
        return self.source()

    async def get(self, file_id: FileId) -> FileResponse:
        self.member_reads.append(file_id)
        if file_id == "source":
            if self.outside_workspace:
                message = "Not in My Drive"
                raise FileNotInScopeError(message)
            return self.source()
        return FileResponse(
            id=file_id,
            name="Destination",
            mime_type=MIME_FOLDER,
            parent_folder_id=None,
            drive_id=DriveId("my-root"),
            modified_time=None,
            can_add_children=self.destination_can_add_children,
        )

    async def list_drives(self) -> list[DriveSummary]:
        return (
            [DriveSummary(id=DriveId("my-root"), name="My Drive", is_personal=True)]
            if self.personal_drive
            else []
        )

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
        assert isinstance(payload, DocumentTransformRequest)
        self.submissions += 1
        self.request = payload
        self.key = idempotency_key
        return await super().create_or_replay(
            kind=kind,
            owner_user_id=owner_user_id,
            idempotency_key=idempotency_key,
            payload=payload,
            admit=admit,
            admit_new=admit_new,
            admit_terminal_replay=admit_terminal_replay,
        )

    async def run(self, request: DocumentIdPolishRequest) -> TransformSubmission:
        self.key = request.idempotency_key
        return await submit_document_by_id(
            request,
            owner_user_id=self.owner,
            files=dependency(self),
            drive=dependency(self),
            tasks=dependency(self),
            enqueuer=dependency(self),
            in_place_enabled=self.in_place_enabled,
        )


def test_only_google_id_is_required_and_defaults_to_both_on_a_copy():
    request = DocumentIdPolishRequest(file_id=FileId("google_ID-123"))
    assert request.mode is TransformMode.BOTH
    assert request.output_strategy is OutputStrategy.COPY
    assert isinstance(request.idempotency_key, uuid.UUID)
    assert DocumentIdPolishRequest.model_json_schema()["required"] == ["file_id"]


@pytest.mark.parametrize(
    "file_id",
    [
        "",
        "x" * 129,
        "https://docs.google.com/document/d/a/edit",
        "a/b",
        "a b",
        "a\n",
        "a%2Fb",
        "a?tab=x",
        "日本語",
        "a\x00",
        123,
        True,
    ],
)
def test_invalid_google_ids_are_rejected_before_provider_work(file_id: str | int | bool):
    with pytest.raises(ValidationError):
        DocumentIdPolishRequest.model_validate({"file_id": file_id})


@pytest.mark.parametrize(
    "field", ["owner_user_id", "principal", "access_token", "expected_modified_time", "unknown"]
)
def test_caller_cannot_inject_identity_or_derived_freshness(field: str):
    with pytest.raises(ValidationError):
        DocumentIdPolishRequest.model_validate({"file_id": "source", field: "secret-canary"})


@pytest.mark.parametrize("mode", list(TransformMode))
@pytest.mark.parametrize("strategy", list(OutputStrategy))
async def test_id_submission_uses_existing_authorized_pipeline(
    mode: TransformMode, strategy: OutputStrategy
):
    harness = ByIdHarness()
    harness.outside_workspace = strategy is OutputStrategy.COPY
    request = DocumentIdPolishRequest(file_id=FileId("source"), mode=mode, output_strategy=strategy)
    result = await harness.run(request)
    assert result.task.id == harness.reservation.task.id
    assert harness.request.mode is mode
    assert harness.request.source.kind == "drive_file_id"
    assert harness.request.source.expected_modified_time == harness.source_modified_time
    assert harness.request.output_strategy is strategy
    assert harness.request.copy_destination_folder_id == (
        "my-root" if strategy is OutputStrategy.COPY else None
    )
    assert harness.submissions == 1
    assert "enqueue" in harness.calls
    assert bool(harness.direct_reads) is (strategy is OutputStrategy.COPY)
    assert ("my-root" in harness.member_reads) is (strategy is OutputStrategy.COPY)


async def test_explicit_destination_is_checked_without_requiring_my_drive_discovery():
    harness = ByIdHarness()
    harness.personal_drive = False
    request = DocumentIdPolishRequest(
        file_id=FileId("source"), copy_destination_folder_id=FileId("destination")
    )
    await harness.run(request)
    assert harness.request.copy_destination_folder_id == "destination"
    assert harness.member_reads == ["destination"]
    assert "unshared-parent" not in harness.member_reads


@pytest.mark.parametrize(
    "case",
    [
        "missing-my-drive",
        "unwritable-destination",
        "in-place-disabled",
        "in-place-shared-source",
        "revoked-source",
        "wrong-provider-id",
    ],
)
async def test_unavailable_or_unauthorized_targets_do_not_enqueue(case: str):
    harness = ByIdHarness()
    request = DocumentIdPolishRequest(file_id=FileId("source"))
    if case == "missing-my-drive":
        harness.personal_drive = False
    elif case == "unwritable-destination":
        harness.destination_can_add_children = False
    elif case == "in-place-disabled":
        harness.in_place_enabled = False
        request.output_strategy = OutputStrategy.IN_PLACE
    elif case == "in-place-shared-source":
        request.output_strategy = OutputStrategy.IN_PLACE
    elif case == "revoked-source":
        harness.file_error = FileNotInScopeError("Google access revoked")
    elif case == "wrong-provider-id":
        harness.source_id = FileId("other-file")
    with pytest.raises((HTTPException, FileNotInScopeError)):
        await harness.run(request)
    assert "enqueue" not in harness.calls
    if case == "in-place-disabled":
        assert harness.direct_reads == harness.member_reads == []


async def test_shared_source_without_a_visible_parent_still_uses_an_explicit_destination():
    harness = ByIdHarness()
    harness.source_parent = None
    await harness.run(DocumentIdPolishRequest(file_id=FileId("source")))
    assert harness.request.copy_destination_folder_id == "my-root"
    assert "enqueue" in harness.calls


@pytest.mark.parametrize(
    "mime",
    [
        MIME_FOLDER,
        "application/pdf",
        "application/vnd.google-apps.presentation",
        "text/markdown",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
)
async def test_id_shortcut_accepts_only_native_google_docs(mime: str):
    harness = ByIdHarness()
    harness.source_mime_type = mime
    with pytest.raises(HTTPException) as error:
        await harness.run(DocumentIdPolishRequest(file_id=FileId("source")))
    assert error.value.status_code == 422
    assert harness.submissions == 0


@pytest.mark.parametrize("freshness", [None, "", "not-a-date", "2026-11-26", "2026-11-26T12:00:00"])
async def test_missing_or_ambiguous_provider_freshness_fails_closed(freshness: str | None):
    harness = ByIdHarness()
    harness.source_modified_time = dependency(freshness)
    with pytest.raises(HTTPException) as error:
        await harness.run(DocumentIdPolishRequest(file_id=FileId("source")))
    assert error.value.status_code == 503
    assert harness.submissions == 0


@pytest.mark.parametrize(
    "options",
    [{"output_name": "Rename"}, {"copy_destination_folder_id": "destination"}],
    ids=["copy-name", "copy-destination"],
)
def test_in_place_cannot_carry_copy_options(options: dict[str, str]):
    with pytest.raises(ValidationError):
        DocumentIdPolishRequest.model_validate(
            {"file_id": "source", "output_strategy": "in_place", **options}
        )


async def test_direct_read_does_not_relax_navigation_or_mutation_scope():
    source = ByIdHarness().source()

    class SharedSourceFiles(GoogleFilesService):
        @override
        def _get_sync(self, file_id: FileId) -> FileResponse:
            assert file_id == "source"
            return source

        @override
        def _get_access_location_sync(self, file_id: FileId) -> GoogleFileAccessLocation:
            return GoogleFileAccessLocation(id=file_id, parents=[FileId("unshared-parent")])

    service = SharedSourceFiles(user=_user(), drive=_FakeDriveService([]))
    assert await service.get_direct(FileId("source")) == source
    for operation in [
        lambda: service.get(FileId("source")),
        lambda: service.rename(FileId("source"), "Other"),
        lambda: service.delete(FileId("source")),
    ]:
        with pytest.raises(FileNotInScopeError):
            await operation()


@pytest.mark.parametrize(
    "capabilities",
    [frozenset(), frozenset({McpCapability.READ}), frozenset({McpCapability.TRANSFORM})],
    ids=["no-grant", "read-only", "transform-without-read"],
)
async def test_id_tool_requires_both_grants_before_provider_reads(
    capabilities: frozenset[McpCapability],
):
    harness = ByIdHarness()
    current = McpApplicationContext(
        principal=ValidatedMcpPrincipal(harness.owner, 1, capabilities),
        drive=dependency(harness),
        files=dependency(harness),
        tasks=dependency(harness),
        aliases=dependency(harness),
    )
    with pytest.raises(McpAuthorizationError):
        await polish_document_by_id(
            DocumentIdPolishRequest(file_id=FileId("source")),
            context=current,
            enqueuer=dependency(object()),
        )
    assert harness.submissions == 0
    assert harness.direct_reads == harness.member_reads == []
