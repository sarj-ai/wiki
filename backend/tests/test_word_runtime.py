from dataclasses import replace
import hashlib
from typing import Any
import uuid

from fastapi import HTTPException
import pytest

from app.document_transform import application as transform_application, word_executor, word_gateway
from app.document_transform.errors import (
    DocumentGatewayError,
    OutputConflictError,
    OutputVerificationError,
    SourceChangedError,
    UnsupportedSourceError,
)
from app.document_transform.schemas import (
    CopyCheckpointStage,
    DocumentOutput,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DriveFileSource,
    OutputStrategy,
    TransformMode,
    WordCopyCheckpoint,
)
from app.document_transform.word_executor import WordTransformExecutor
from app.document_transform.word_gateway import WORD_MIME, GoogleWordGateway, WordSource
from app.files.errors import DriveUpstreamError
from app.files.schemas import FileResponse
from app.identifiers import FileId, TaskId
from app.tasks import service as task_service
from tests.support import WordCreateDriveFake as CreateDrive, WordDriveFake as FakeDrive
from tests.test_word_package import RUN, package


def request(
    mode: TransformMode = TransformMode.FORMAT, strategy: OutputStrategy = OutputStrategy.COPY
) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="time"
        ),
        mode=mode,
        output_strategy=strategy,
    )


class Harness:
    def __init__(self) -> None:
        self.operation = uuid.uuid7()
        self.checkpoints: list[WordCopyCheckpoint] = []
        self.source = WordSource(
            {
                "id": "source",
                "name": "security policy.docx",
                "parent": "folder",
                "modifiedTime": "time",
            },
            package(f"<w:p>{RUN}</w:p>"),
        )
        self.output = DocumentOutput(
            file_id=FileId("output"),
            name="Security Policy.docx",
            parent_folder_id=FileId("folder"),
            media_type=WORD_MIME,
        )
        self.recovered: DocumentOutput | None = None
        self.create_error: Exception | None = None
        self.verify_error: Exception | None = None
        self.save_error: Exception | None = None
        self.next_source: WordSource | None = None
        self.reservations = 0
        self.created_ids: list[FileId] = []
        self.creates = 0
        self.loads = 0
        self.verifies = 0

    async def reserve_word_id(self) -> FileId:
        self.reservations += 1
        return FileId("output")

    async def load_word(self, source: DriveFileSource) -> WordSource:
        self.loads += 1
        return self.next_source if self.loads > 1 and self.next_source is not None else self.source

    async def recover_word(
        self, operation_id: uuid.UUID, *, source: WordSource, output_sha256: str, output_id: FileId
    ) -> DocumentOutput | None:
        return self.recovered

    async def create_word(
        self,
        operation_id: uuid.UUID,
        *,
        source: WordSource,
        content: bytes,
        name: str,
        output_id: FileId,
    ) -> DocumentOutput:
        self.created_ids.append(output_id)
        self.creates += 1
        if self.create_error is not None:
            raise self.create_error
        return self.output

    async def verify_word(
        self,
        output: DocumentOutput,
        *,
        expected: bytes,
        operation_id: uuid.UUID,
        source: WordSource,
    ) -> None:
        self.verifies += 1
        if self.verify_error is not None:
            raise self.verify_error

    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        checkpoint: DocumentTransformCheckpoint,
        expected_generation: int,
        lease_token: uuid.UUID,
    ) -> int:
        if self.save_error is not None:
            raise self.save_error
        assert isinstance(checkpoint, WordCopyCheckpoint)
        assert task_id == self.operation
        assert expected_generation == len(self.checkpoints)
        assert lease_token == self.operation
        self.checkpoints.append(checkpoint)
        return len(self.checkpoints)

    async def record_resource(
        self, task_id: TaskId, *, lease_token: uuid.UUID, resource_id: FileId
    ) -> None:
        assert resource_id == self.output.file_id

    def executor(self):
        return WordTransformExecutor(
            gateway=self,
            checkpoint_store=self,
            task_id=TaskId(self.operation),
            lease_token=self.operation,
            checkpoint=self.checkpoints[-1] if self.checkpoints else None,
            checkpoint_generation=len(self.checkpoints),
        )

    async def run(self):
        return await self.executor().execute(operation_id=self.operation, request=request())


async def test_word_copy_is_durable_encrypted_and_completed_replay_is_read_only():
    h = Harness()
    result = await h.run()
    assert [c.stage for c in h.checkpoints] == [
        CopyCheckpointStage.PLANNED,
        CopyCheckpointStage.CREATING,
        CopyCheckpointStage.CREATED,
        CopyCheckpointStage.VERIFIED,
    ]
    assert result.output.media_type == WORD_MIME
    assert result.needs_review
    assert not result.llm_invoked
    assert result.output_revision_id is None
    assert result.applied_patch_count == 1
    serialized = task_service._serialize_checkpoint(h.checkpoints[-1])
    assert set(serialized) == {"kind", "version", "checkpoint_ciphertext"}
    assert serialized["kind"] == "word_copy"
    assert "security policy" not in str(serialized)
    assert task_service._deserialize_checkpoint(serialized) == h.checkpoints[-1]
    h.recovered = h.output
    assert await h.run() == result
    assert h.creates == 1
    assert len(h.checkpoints) == 4
    assert h.verifies == 2


async def test_ambiguous_upload_retries_only_reserved_id_and_recovers_later():
    h = Harness()
    h.create_error = DocumentGatewayError()
    with pytest.raises(DocumentGatewayError):
        await h.run()
    assert h.checkpoints[-1].stage is CopyCheckpointStage.CREATING
    with pytest.raises(DocumentGatewayError):
        await h.run()
    assert h.creates == 2
    h.recovered = h.output
    result = await h.run()
    assert result.output == h.output
    assert h.reservations == 1
    assert h.created_ids == [FileId("output"), FileId("output")]
    assert h.creates == 2


@pytest.mark.parametrize(
    ("mode", "strategy"),
    [
        (TransformMode.BOTH, OutputStrategy.COPY),
        (TransformMode.UNSLOP, OutputStrategy.COPY),
        (TransformMode.FORMAT, OutputStrategy.IN_PLACE),
    ],
)
async def test_unsupported_modes_fail_before_any_provider_read(
    mode: TransformMode, strategy: OutputStrategy
):
    h = Harness()
    with pytest.raises(UnsupportedSourceError):
        await h.executor().execute(operation_id=h.operation, request=request(mode, strategy))
    assert h.loads == 0


async def test_uncheckpointed_recovered_output_fails_closed():
    h = Harness()
    h.recovered = h.output
    with pytest.raises(OutputConflictError):
        await h.run()
    assert h.checkpoints == []
    assert h.creates == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sha256", "wrong"),
        ("output_sha256", "wrong"),
        ("transformer_version", "older"),
        ("target_font", "Arial"),
        ("changed_run_count", 3),
        ("output_name", "Unexpected.docx"),
        ("request_sha256", "wrong"),
        ("output", None),
    ],
)
async def test_tampered_checkpoint_is_rejected_before_mutation(field: str, value: Any):
    h = Harness()
    await h.run()
    h.checkpoints[-1] = h.checkpoints[-1].model_copy(update={field: value})
    h.creates = 0
    with pytest.raises(OutputConflictError):
        await h.run()
    assert h.creates == 0


async def test_output_readback_failure_never_claims_verified():
    h = Harness()
    h.verify_error = OutputVerificationError()
    with pytest.raises(OutputVerificationError):
        await h.run()
    assert h.checkpoints[-1].stage is CopyCheckpointStage.CREATED


async def test_source_change_after_upload_never_claims_success():
    h = Harness()
    h.next_source = replace(h.source, content=b"changed")
    with pytest.raises(SourceChangedError):
        await h.run()
    assert h.checkpoints[-1].stage is CopyCheckpointStage.CREATED


async def test_lease_failure_before_creating_prevents_upload():
    h = Harness()
    h.save_error = RuntimeError("lost lease")
    with pytest.raises(RuntimeError, match="lost lease"):
        await h.run()
    assert h.creates == 0


def metadata(content: bytes, **changes: Any) -> dict[str, Any]:
    return {
        "id": "source",
        "name": "Security.docx",
        "mimeType": WORD_MIME,
        "parents": ["folder"],
        "modifiedTime": "time",
        "version": "1",
        "size": str(len(content)),
        "md5Checksum": hashlib.md5(content, usedforsecurity=False).hexdigest(),
        "trashed": False,
        "capabilities": {"canDownload": True},
        **changes,
    }


def test_binary_read_binds_metadata_and_checksum_without_docs_api(monkeypatch: pytest.MonkeyPatch):
    content = b"binary"
    gateway = object.__new__(GoogleWordGateway)
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: metadata(content))
    monkeypatch.setattr(gateway, "_execute_bounded_bytes", lambda *_args, **_kwargs: content)
    monkeypatch.setattr(gateway, "_drive", FakeDrive)
    loaded = gateway._read_word_sync(FileId("source"))
    assert loaded.content == content
    versions = iter([metadata(content), metadata(content, version="2")])
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: next(versions))
    with pytest.raises(SourceChangedError):
        gateway._read_word_sync(FileId("source"))


@pytest.mark.parametrize(
    "changes",
    [
        {"mimeType": "application/vnd.google-apps.document"},
        {"trashed": True},
        {"capabilities": {"canDownload": False}},
        {"parents": []},
        {"size": "999999999999"},
        {"version": None},
    ],
    ids=["wrong-mime", "trashed", "download-denied", "no-parent", "oversized", "no-version"],
)
def test_invalid_binary_metadata_rejected(changes: dict[str, Any]):
    with pytest.raises(UnsupportedSourceError):
        word_gateway._binding(metadata(b"data", **changes))


def test_source_checksum_mismatch_is_not_a_snapshot(monkeypatch: pytest.MonkeyPatch):
    gateway = object.__new__(GoogleWordGateway)
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: metadata(b"data"))
    monkeypatch.setattr(gateway, "_execute_bounded_bytes", lambda *_args, **_kwargs: b"edit")
    monkeypatch.setattr(gateway, "_drive", FakeDrive)
    with pytest.raises(SourceChangedError):
        gateway._read_word_sync(FileId("source"))


def test_copy_name_is_title_case_and_does_not_stack_suffixes():
    assert word_executor._output_name(None, "security policy.docx") == "Security Policy.docx"
    assert word_executor._output_name(None, "Security Policy.docx") == "Security Policy.docx"
    assert word_executor._output_name("Chosen Name", "source.docx") == "Chosen Name.docx"
    assert word_executor._output_name("Chosen Name.DOCX", "source.docx") == "Chosen Name.DOCX"


class WordFiles:
    async def get(self, file_id: FileId) -> FileResponse:
        return FileResponse(
            id=file_id,
            name="Security.docx",
            mime_type=WORD_MIME,
            parent_folder_id=FileId("folder"),
            drive_id=None,
            modified_time="time",
        )


async def test_admission_accepts_only_word_format_copy():
    await transform_application.validate_transform_admission(request(), WordFiles())
    for mode, strategy in (
        (TransformMode.BOTH, OutputStrategy.COPY),
        (TransformMode.UNSLOP, OutputStrategy.COPY),
        (TransformMode.FORMAT, OutputStrategy.IN_PLACE),
    ):
        with pytest.raises(HTTPException) as error:
            await transform_application.validate_transform_admission(
                request(mode, strategy), WordFiles()
            )
        assert error.value.status_code == 422


@pytest.mark.parametrize(
    "changed",
    [{"name": "Renamed.docx"}, {"media_type": "application/vnd.google-apps.document"}],
    ids=["renamed", "converted"],
)
async def test_create_or_recovery_cannot_change_the_saved_name_or_type(changed: dict[str, str]):
    h = Harness()
    h.output = h.output.model_copy(update=changed)
    with pytest.raises(OutputConflictError):
        await h.run()
    assert h.checkpoints[-1].stage is CopyCheckpointStage.CREATING
    h.recovered = h.output
    with pytest.raises(OutputConflictError):
        await h.run()
    assert h.creates == 1


def test_create_checks_source_before_dispatch_and_preserves_docx_mime(
    monkeypatch: pytest.MonkeyPatch,
):
    h = Harness()
    drive = CreateDrive()
    gateway = object.__new__(GoogleWordGateway)
    content = h.source.content
    binding = word_gateway._binding(metadata(content))
    source = WordSource(binding, content)
    monkeypatch.setattr(gateway, "_drive", lambda: drive)
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: metadata(content))
    properties = word_gateway._properties(h.operation, content, hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(
        gateway,
        "_execute_mutation_dict",
        lambda _: metadata(content, id="output", appProperties=properties),
    )
    output = gateway._create_word_sync(
        h.operation, source, content, "Security.docx", FileId("output")
    )
    assert output.media_type == WORD_MIME
    assert drive.calls[0]["body"]["mimeType"] == WORD_MIME
    assert drive.calls[0]["body"]["id"] == "output"
    assert drive.calls[0]["media_body"].mimetype() == WORD_MIME
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: metadata(content, version="2"))
    with pytest.raises(SourceChangedError):
        gateway._create_word_sync(h.operation, source, content, "Security.docx", FileId("output"))
    assert len(drive.calls) == 1


@pytest.mark.parametrize(
    "response",
    [{}, {"name": "Wrong.docx"}, {"appProperties": {}}],
    ids=["empty", "only-name", "only-properties"],
)
def test_create_response_failure_cannot_be_reported_as_success(
    monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]
):
    content = b"binary"
    source = WordSource(word_gateway._binding(metadata(content)), content)
    gateway = object.__new__(GoogleWordGateway)
    monkeypatch.setattr(gateway, "_drive", CreateDrive)
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: metadata(content))
    monkeypatch.setattr(gateway, "_execute_mutation_dict", lambda _: response)
    with pytest.raises(DocumentGatewayError):
        gateway._create_word_sync(uuid.uuid7(), source, content, "Security.docx", FileId("output"))


def test_reserved_id_is_validated_and_recovery_reads_that_id(monkeypatch: pytest.MonkeyPatch):
    h = Harness()
    gateway = object.__new__(GoogleWordGateway)
    monkeypatch.setattr(gateway, "_drive", FakeDrive)
    monkeypatch.setattr(FakeDrive, "generateIds", lambda *_args, **_kwargs: object(), raising=False)
    monkeypatch.setattr(gateway, "_execute_dict", lambda _: {"ids": ["output"]})
    assert gateway._reserve_word_id_sync() == "output"
    monkeypatch.setattr(gateway, "_execute_dict", lambda _: {"ids": []})
    with pytest.raises(DocumentGatewayError):
        gateway._reserve_word_id_sync()
    observed: list[FileId] = []

    def missing(file_id: FileId) -> dict[str, Any]:
        observed.append(file_id)
        raise DocumentGatewayError(http_status=404)

    monkeypatch.setattr(gateway, "_word_metadata_sync", missing)
    assert gateway._recover_word_sync(h.operation, h.source, "hash", FileId("output")) is None
    assert observed == [FileId("output")]


def test_same_id_conflict_recovers_exact_operation_without_uploading_another_file(
    monkeypatch: pytest.MonkeyPatch,
):
    h = Harness()
    drive = CreateDrive()
    gateway = object.__new__(GoogleWordGateway)
    content = h.source.content
    source = WordSource(word_gateway._binding(metadata(content)), content)
    properties = word_gateway._properties(h.operation, content, hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(gateway, "_drive", lambda: drive)
    monkeypatch.setattr(
        gateway,
        "_word_metadata_sync",
        lambda file_id: metadata(
            content,
            **({"id": "output", "appProperties": properties} if file_id == "output" else {}),
        ),
    )

    def conflict(_request: object) -> dict[str, Any]:
        raise DocumentGatewayError(http_status=409, retryable=False)

    monkeypatch.setattr(gateway, "_execute_mutation_dict", conflict)
    output = gateway._create_word_sync(
        h.operation, source, content, "Security.docx", FileId("output")
    )
    assert output.file_id == "output"
    assert len(drive.calls) == 1
    assert drive.calls[0]["body"]["id"] == "output"
    properties["sarjWordTransformOperation"] = "unrelated"
    with pytest.raises(OutputConflictError):
        gateway._create_word_sync(h.operation, source, content, "Security.docx", FileId("output"))
    assert len(drive.calls) == 2
    assert {call["body"]["id"] for call in drive.calls} == {"output"}


@pytest.mark.parametrize("change", ["operation", "source", "output", "file-id", "parent", "mime"])
def test_direct_id_recovery_rejects_changed_identity(monkeypatch: pytest.MonkeyPatch, change: str):
    h = Harness()
    gateway = object.__new__(GoogleWordGateway)
    properties = word_gateway._properties(h.operation, h.source.content, "hash")
    response = metadata(h.source.content, id="output", appProperties=properties)
    match change:
        case "operation" | "source" | "output":
            key = {
                "operation": "sarjWordTransformOperation",
                "source": "sarjWordSourceSha256",
                "output": "sarjWordOutputSha256",
            }[change]
            properties[key] = "wrong"
        case "file-id":
            response["id"] = "wrong"
        case "parent":
            response["parents"] = ["elsewhere"]
        case _:
            response["mimeType"] = "application/vnd.google-apps.document"
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: response)
    with pytest.raises((OutputConflictError, UnsupportedSourceError)):
        gateway._recover_word_sync(h.operation, h.source, "hash", FileId("output"))


async def test_created_output_disappearance_does_not_recreate():
    h = Harness()
    await h.run()
    with pytest.raises(DocumentGatewayError):
        await h.run()
    assert h.creates == 1
    assert h.reservations == 1


async def test_final_readback_checks_app_properties(monkeypatch: pytest.MonkeyPatch):
    h = Harness()
    gateway = object.__new__(GoogleWordGateway)
    monkeypatch.setattr(gateway, "_files", WordFiles(), raising=False)
    content = h.source.content
    response = metadata(content, id="output", name=h.output.name)
    loaded = WordSource(word_gateway._binding(response), content)
    monkeypatch.setattr(gateway, "_read_word_sync", lambda _: loaded)
    monkeypatch.setattr(gateway, "_word_metadata_sync", lambda _: response)
    with pytest.raises(OutputVerificationError):
        await gateway.verify_word(
            h.output, expected=content, operation_id=h.operation, source=h.source
        )
    response["appProperties"] = word_gateway._properties(
        h.operation, content, hashlib.sha256(content).hexdigest()
    )
    await gateway.verify_word(h.output, expected=content, operation_id=h.operation, source=h.source)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 503, 504])
async def test_final_scope_read_preserves_retry_classification(
    monkeypatch: pytest.MonkeyPatch, status: int
):
    h = Harness()
    gateway = object.__new__(GoogleWordGateway)
    files = WordFiles()
    upstream = DriveUpstreamError(status)

    async def unavailable(_file_id: FileId) -> FileResponse:
        raise upstream

    monkeypatch.setattr(files, "get", unavailable)
    monkeypatch.setattr(gateway, "_files", files, raising=False)
    expected_error = DocumentGatewayError if status >= 429 else OutputVerificationError
    with pytest.raises(expected_error) as caught:
        await gateway.verify_word(
            h.output,
            expected=h.source.content,
            operation_id=h.operation,
            source=h.source,
        )
    assert caught.value.__cause__ is upstream
    if isinstance(caught.value, DocumentGatewayError):
        assert caught.value.retryable
        assert caught.value.http_status == upstream.http_status


async def test_transient_verification_recovers_without_another_upload():
    h = Harness()
    h.verify_error = DocumentGatewayError(retryable=True, http_status=503)
    with pytest.raises(DocumentGatewayError):
        await h.run()
    assert h.checkpoints[-1].stage is CopyCheckpointStage.CREATED
    h.recovered = h.output
    h.verify_error = None
    result = await h.run()
    assert result.output == h.output
    assert h.creates == 1
    assert h.reservations == 1
    assert h.checkpoints[-1].stage is CopyCheckpointStage.VERIFIED
