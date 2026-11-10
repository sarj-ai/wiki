from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import NamedTuple, override
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.errors import DocumentGatewayError, UnsupportedTabMetadataError
from app.document_transform.gateway import InPlaceStageResult, OutputVerification
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.schemas import (
    CopyCheckpointPayload,
    CopyCheckpointStage,
    InPlaceCheckpointStage,
    TabNamingCheckpoint,
    TransformMode,
)
from app.document_transform.tab_metadata import tab_naming_invariant_sha256
from app.document_transform.tab_names import NativeTabProperties, TabNamingPlan, TabRename
from app.identifiers import DocsRevisionId, DocsTabId, FileId, TaskId
from tests.test_document_transform import (
    _CheckpointStore,
    _copy_executor,
    _CopyCheckpointStore,
    _CrashReplayInPlaceGateway,
    _Gateway,
    _Generator,
    _in_place_request,
    _native_copy_gateway,
    _native_copy_request,
    _replay_executor,
)
from tests.test_document_transform_tab_metadata import _document


class _RejectedNativeTabs(GoogleDocumentGateway):
    def __init__(self) -> None:
        self.document = _document()
        self.after = deepcopy(self.document)
        self.http_status = 400
        self.attempts = 0

    @override
    def _get_document_sync(self, file_id: FileId) -> dict[str, JsonValue]:
        assert file_id == "doc"
        return self.document

    @override
    async def apply_in_place_stage(
        self,
        *,
        document_id: FileId,
        required_revision_id: DocsRevisionId,
        marker_name: str,
        marker_range: Mapping[str, JsonValue],
        requests: Sequence[Mapping[str, JsonValue]],
    ) -> InPlaceStageResult:
        assert document_id == "doc"
        assert required_revision_id == "r1"
        assert marker_name
        assert marker_range
        assert len(requests) == 1
        self.attempts += 1
        self.document = self.after
        raise DocumentGatewayError(retryable=False, http_status=self.http_status)


@pytest.fixture
def native_tab_gateway() -> _RejectedNativeTabs:
    return _RejectedNativeTabs()


@pytest.mark.parametrize("http_status", [400, 403, 409, 429, 500])
async def test_only_unchanged_bad_request_is_an_optional_tab_decline(
    http_status: int, native_tab_gateway: _RejectedNativeTabs
):
    gateway = native_tab_gateway
    gateway.http_status = http_status
    checkpoint = await gateway.plan_tab_naming(
        document_id=FileId("doc"),
        title="Plan",
        operation_id=uuid.uuid7(),
        required_revision_id=DocsRevisionId("r1"),
    )
    assert checkpoint is not None
    expected = UnsupportedTabMetadataError if http_status == 400 else DocumentGatewayError
    with pytest.raises(expected):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert gateway.attempts == 1
    assert gateway.document == _document()


@pytest.mark.parametrize("change", ["revision", "title", "body", "header", "footer", "marker"])
async def test_rejected_tab_write_never_declines_a_changed_or_partially_written_document(
    change: str, native_tab_gateway: _RejectedNativeTabs
):
    gateway = native_tab_gateway
    checkpoint = await gateway.plan_tab_naming(
        document_id=FileId("doc"),
        title="Plan",
        operation_id=uuid.uuid7(),
        required_revision_id=DocsRevisionId("r1"),
    )
    assert checkpoint is not None
    tab = gateway.after["tabs"][0]
    match change:
        case "revision":
            gateway.after["revisionId"] = "r2"
        case "title":
            tab["tabProperties"]["title"] = "Concurrent title"
        case "marker":
            tab["documentTab"]["namedRanges"] = {checkpoint.marker_name: {"unexpected": True}}
        case _:
            key = {"body": "body", "header": "headers", "footer": "footers"}[change]
            tab["documentTab"][key] = {"changed": True}
    with pytest.raises(DocumentGatewayError):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert gateway.attempts == 1


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("image_field", ["contentUri", "sourceUri"])
async def test_decline_preserves_versioned_image_identity_guards(
    native_tab_gateway: _RejectedNativeTabs, image_field: str, *, legacy: bool
):
    gateway = native_tab_gateway
    gateway.document["tabs"][0]["documentTab"]["inlineObjects"] = {
        "photo": {
            "inlineObjectProperties": {
                "embeddedObject": {
                    "imageProperties": {
                        "contentUri": "https://example.test/temporary-1",
                        "sourceUri": "https://example.test/original-photo",
                    }
                }
            }
        }
    }
    gateway.after = deepcopy(gateway.document)
    properties = gateway.after["tabs"][0]["documentTab"]["inlineObjects"]["photo"][
        "inlineObjectProperties"
    ]["embeddedObject"]["imageProperties"]
    properties[image_field] = "https://example.test/changed"
    checkpoint = await gateway.plan_tab_naming(
        document_id=FileId("doc"),
        title="Plan",
        operation_id=uuid.uuid7(),
        required_revision_id=DocsRevisionId("r1"),
    )
    assert checkpoint is not None
    if legacy:
        checkpoint = checkpoint.model_copy(
            update={
                "invariant_version": None,
                "invariant_sha256": tab_naming_invariant_sha256(gateway.document),
            }
        )
    expected = (
        UnsupportedTabMetadataError
        if image_field == "contentUri" and not legacy
        else DocumentGatewayError
    )
    with pytest.raises(expected):
        await gateway.apply_tab_naming(document_id=FileId("doc"), checkpoint=checkpoint)
    assert gateway.attempts == 1


def _pending_naming(revision: DocsRevisionId) -> TabNamingCheckpoint:
    tab_id = DocsTabId("tab-1")
    return TabNamingCheckpoint(
        plan=TabNamingPlan(
            (NativeTabProperties(tab_id, "Tab 1"),),
            (TabRename(tab_id, "Tab 1", "Decision"),),
        ),
        revision_id=revision,
        marker_name="pending-tabs",
        marker_range={"startIndex": 1, "endIndex": 2, "tabId": tab_id},
        invariant_sha256="0" * 64,
    )


class _CopyDeclinedTabs(_Gateway):
    def __init__(self) -> None:
        super().__init__(
            _native_copy_gateway().snapshot,
            verification=OutputVerification(True, True, True),
        )
        self.naming_attempts = 0

    @override
    async def plan_tab_naming(
        self,
        *,
        document_id: FileId,
        title: str,
        operation_id: uuid.UUID,
        required_revision_id: DocsRevisionId,
        prefer_document_title: bool = False,
    ) -> TabNamingCheckpoint:
        return _pending_naming(required_revision_id)

    @override
    async def apply_tab_naming(
        self, *, document_id: FileId, checkpoint: TabNamingCheckpoint
    ) -> DocsRevisionId:
        self.naming_attempts += 1
        raise UnsupportedTabMetadataError


class _CopyScenario(NamedTuple):
    gateway: _CopyDeclinedTabs
    store: _CopyCheckpointStore
    task_id: TaskId


@pytest.fixture
def copy_decline() -> _CopyScenario:
    return _CopyScenario(_CopyDeclinedTabs(), _CopyCheckpointStore(), TaskId(uuid.uuid7()))


async def test_verified_copy_survives_rejected_tab_write_and_never_replays_it(
    copy_decline: _CopyScenario,
):
    gateway, store, task_id = copy_decline
    request = _native_copy_request().model_copy(update={"mode": TransformMode.FORMAT})
    result = await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
        operation_id=task_id, request=request
    )
    checkpoint = CopyCheckpointPayload.model_validate_json(
        store.checkpoints[-1].model_dump_json()
    ).root
    assert checkpoint.stage is CopyCheckpointStage.VERIFIED
    assert checkpoint.tab_naming is None
    assert checkpoint.tab_naming_declined_reason == "unsupported_metadata"
    assert result.validation.source_unchanged
    assert result.validation.protected_content_preserved
    assert result.validation.structure_preserved
    assert "tab_naming_preserved" in {finding.code for finding in result.findings}
    replayed = await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint
    ).execute(operation_id=task_id, request=request)
    assert replayed == result
    assert gateway.naming_attempts == 1
    assert gateway.applies == 1


class _InPlaceDeclinedTabs(_CrashReplayInPlaceGateway):
    def __init__(self) -> None:
        super().__init__("Moreover, we approved the plan.", crash_stage="never")
        self.naming_attempts = 0

    @override
    async def plan_tab_naming(
        self,
        *,
        document_id: FileId,
        title: str,
        operation_id: uuid.UUID,
        required_revision_id: DocsRevisionId,
        prefer_document_title: bool = False,
    ) -> TabNamingCheckpoint:
        return _pending_naming(required_revision_id)

    @override
    async def apply_tab_naming(
        self, *, document_id: FileId, checkpoint: TabNamingCheckpoint
    ) -> DocsRevisionId:
        self.naming_attempts += 1
        raise UnsupportedTabMetadataError


class _InPlaceScenario(NamedTuple):
    gateway: _InPlaceDeclinedTabs
    store: _CheckpointStore
    generator: _Generator
    task_id: TaskId


@pytest.fixture
def in_place_decline() -> _InPlaceScenario:
    gateway = _InPlaceDeclinedTabs()
    return _InPlaceScenario(
        gateway, _CheckpointStore(gateway.events), _Generator(), TaskId(uuid.uuid7())
    )


async def test_in_place_rejected_tab_write_retains_verified_body_and_durable_decline(
    in_place_decline: _InPlaceScenario,
):
    gateway, store, generator, task_id = in_place_decline
    request = _in_place_request(TransformMode.BOTH)
    result = await _replay_executor(
        gateway=gateway, generator=generator, store=store, task_id=task_id
    ).execute(operation_id=task_id, request=request)
    checkpoint = store.checkpoints[-1]
    assert checkpoint.stage is InPlaceCheckpointStage.COMPLETE
    assert checkpoint.tab_naming is None
    assert checkpoint.tab_naming_declined_reason == "unsupported_metadata"
    assert result.changed
    assert result.validation.protected_content_preserved
    replayed = await _replay_executor(
        gateway=gateway, generator=generator, store=store, task_id=task_id, checkpoint=checkpoint
    ).execute(operation_id=task_id, request=request)
    # The shared fixture omits the verification URL but includes it on source reload.
    assert replayed.model_dump(exclude={"output": {"web_url"}}) == result.model_dump(
        exclude={"output": {"web_url"}}
    )
    assert gateway.naming_attempts == 1
    assert gateway.mutations == {"headers": 1, "content": 1}
