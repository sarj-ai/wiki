from copy import deepcopy
from dataclasses import replace
from typing import Any, override
import uuid

import pytest

from app.document_transform import google_gateway as gateway_module
from app.document_transform.brand import SarjBrandProfile, build_copy_mutations, mutation_sha256
from app.document_transform.errors import DocumentGatewayError, SourceChangedError
from app.document_transform.gateway import OutputDraft, OutputVerification, SourceSnapshot
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.ir import parse_google_document
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformRequest,
    DriveFileSource,
    TabNamingCheckpoint,
    TransformMode,
)
from app.document_transform.tab_names import TabNamingPlan
from app.drive.schemas import MIME_DOC
from app.identifiers import DocsRevisionId, DocsTabId, FileId, TaskId
from tests.test_document_transform import _copy_executor, _CopyCheckpointStore, _Gateway
from tests.test_document_transform_protected_content import _document


def _native(*, mixed: bool = False) -> dict[str, Any]:
    document = _document()
    tab = document["tabs"][0]["documentTab"]
    tab["documentStyle"] = {"defaultHeaderId": "custom-header"}
    tab["headers"] = {
        "custom-header": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Custom furniture\n"}}]}}
            ]
        }
    }
    if mixed:
        other = deepcopy(document["tabs"][0])
        other["tabProperties"]["tabId"] = "tab-b"
        other["documentTab"]["documentStyle"] = {"defaultHeaderId": "owned-header"}
        other["documentTab"]["headers"] = {"owned-header": {"content": []}}
        document["tabs"].append(other)
    return document


def _draft() -> OutputDraft:
    return OutputDraft(
        output=DocumentOutput(
            file_id=FileId("output"), name="Output", parent_folder_id=FileId("folder")
        ),
        document_id=FileId("output"),
        tab_id=DocsTabId("tab-a"),
        revision_id=DocsRevisionId("revision-1"),
        source_sha256="source",
    )


def _gateway(monkeypatch: pytest.MonkeyPatch, document: dict[str, Any]) -> GoogleDocumentGateway:
    gateway = object.__new__(GoogleDocumentGateway)
    gateway._logo_uri = "https://example.test/logo.png"
    monkeypatch.setattr(gateway, "_get_document_sync", lambda _document_id: document)
    monkeypatch.setattr(
        gateway,
        "_execute_mutation_dict",
        lambda _request: pytest.fail("recovery must not create or populate headers"),
    )
    return gateway


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_persisted_header_ownership_rebuilds_identical_requests_and_recovers_marker(
    monkeypatch: pytest.MonkeyPatch, *, mixed: bool, ambiguous: bool
) -> None:
    document = _native(mixed=mixed)
    original = parse_google_document(document, title="Source")
    gateway = _gateway(monkeypatch, document)
    first = gateway._prepare_brand_shell_sync(uuid.uuid7(), _draft())
    owned = {str(header.tab_id): str(header.header_id) for header in first.headers}
    requests = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=first.headers,
        logo_uri=gateway._logo_uri,
    )
    digest = mutation_sha256(requests)
    if mixed:
        document["tabs"][1]["documentTab"]["headers"]["owned-header"]["content"] = [
            {"paragraph": {"elements": [{"textRun": {"content": "Source\t"}}]}}
        ]
    document["revisionId"] = "revision-2"
    pending = replace(
        _draft(),
        revision_id=DocsRevisionId("revision-2"),
        pending_mutation_sha256=digest,
        applied_mutation_sha256=None if ambiguous else digest,
        mutation_base_revision_sha256=gateway_module._revision_sha256(DocsRevisionId("revision-1")),
    )
    before = deepcopy(document)
    recovered = gateway._prepare_brand_shell_sync(uuid.uuid7(), pending, owned)
    replay = build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=recovered.headers,
        logo_uri=gateway._logo_uri,
    )
    assert mutation_sha256(replay) == digest
    assert recovered.headers == first.headers
    assert recovered.brand_warnings == ("header_conflict:tab-a",)
    assert document == before
    marker = f"sarj-transform-copy-{digest}"
    marker_range = gateway_module._terminal_body_marker_range(document)
    assert marker_range is not None
    document["tabs"][-1]["documentTab"]["namedRanges"] = {
        marker: {"namedRanges": [{"name": marker, "ranges": [marker_range]}]}
    }
    properties: list[object] = []
    monkeypatch.setattr(
        gateway, "_update_properties_sync", lambda _document_id, value: properties.append(value)
    )
    applied = gateway._apply_requests_sync(recovered, digest, replay, recovered.revision_id)
    assert applied.applied_mutation_sha256 == digest
    assert properties == [{gateway_module._PROP_MUTATION: digest}]


@pytest.mark.parametrize(
    "change", ["missing_tab", "wrong_id", "missing_segment", "missing_reference", "duplicate_tab"]
)
def test_owned_header_recovery_rejects_changed_binding(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    document = _native(mixed=True)
    tab = document["tabs"][1]["documentTab"]
    match change:
        case "missing_tab":
            document["tabs"].pop()
        case "wrong_id":
            tab["documentStyle"]["defaultHeaderId"] = "custom-header"
        case "missing_segment":
            tab["headers"] = {}
        case "missing_reference":
            tab["documentStyle"] = {}
        case _:
            document["tabs"].append(deepcopy(document["tabs"][1]))
    gateway = _gateway(monkeypatch, document)
    with pytest.raises(SourceChangedError):
        gateway._prepare_brand_shell_sync(uuid.uuid7(), _draft(), {"tab-b": "owned-header"})


def test_uncommitted_owned_header_rejects_concurrent_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _native(mixed=True)
    document["tabs"][1]["documentTab"]["headers"]["owned-header"]["content"] = [
        {"paragraph": {"elements": [{"textRun": {"content": "Concurrent authored text\n"}}]}}
    ]
    gateway = _gateway(monkeypatch, document)
    with pytest.raises(SourceChangedError):
        gateway._prepare_brand_shell_sync(uuid.uuid7(), _draft(), {"tab-b": "owned-header"})


@pytest.mark.parametrize("base", [None, "wrong-base", "original"])
@pytest.mark.parametrize("marker", ["absent", "wrong_range", "wrong_digest"])
def test_pending_recovery_never_rebases_onto_concurrent_revision_without_exact_marker(
    monkeypatch: pytest.MonkeyPatch, base: str | None, marker: str
) -> None:
    document = _native()
    document["revisionId"] = "concurrent"
    gateway = _gateway(monkeypatch, document)
    monkeypatch.setattr(
        gateway,
        "_update_properties_sync",
        lambda *_args: pytest.fail("must reject before metadata writes"),
    )
    digest = "a" * 64
    draft = replace(
        _draft(),
        revision_id=DocsRevisionId("concurrent"),
        pending_mutation_sha256=digest,
        mutation_base_revision_sha256=gateway_module._revision_sha256(DocsRevisionId("revision-1"))
        if base == "original"
        else base,
    )
    if marker != "absent":
        name = f"sarj-transform-copy-{digest if marker == 'wrong_range' else 'b' * 64}"
        marker_range = gateway_module._terminal_body_marker_range(document)
        assert marker_range is not None
        if marker == "wrong_range":
            marker_range |= {"startIndex": 0}
        document["tabs"][0]["documentTab"]["namedRanges"] = {
            name: {"namedRanges": [{"name": name, "ranges": [marker_range]}]}
        }
    recovered = gateway._prepare_brand_shell_sync(uuid.uuid7(), draft, {})
    with pytest.raises(SourceChangedError):
        gateway._apply_requests_sync(
            recovered, digest, [{"insertText": {"text": "synthetic"}}], recovered.revision_id
        )


class _OutageGateway(_Gateway):
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.native = _native()
        super().__init__(
            SourceSnapshot(
                title="Source",
                parent_folder_id=FileId("folder"),
                modified_time="modified",
                revision_id=DocsRevisionId("revision-1"),
                media_type=MIME_DOC,
                google_document=deepcopy(self.native),
            ),
            verification=OutputVerification(True, True, False),
        )
        self.native_gateway = _gateway(monkeypatch, self.native)
        self.naming_attempts = 0
        self.body_digests: list[str] = []

    @override
    async def prepare_brand_shell(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        managed_headers: dict[str, str] | None = None,
    ) -> OutputDraft:
        return self.native_gateway._prepare_brand_shell_sync(operation_id, draft, managed_headers)

    @override
    async def apply_requests_once(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: DocsRevisionId,
    ) -> OutputDraft:
        del operation_id, required_revision_id
        self.body_digests.append(mutation_sha256)
        if draft.applied_mutation_sha256 == mutation_sha256:
            return draft
        assert draft.pending_mutation_sha256 is None
        self.applies += 1
        self.requests = requests
        self.native["revisionId"] = "revision-2"
        self.draft = replace(
            draft,
            revision_id=DocsRevisionId("revision-2"),
            headers=(),
            applied_mutation_sha256=mutation_sha256,
            pending_mutation_sha256=mutation_sha256,
        )
        return self.draft

    @override
    async def plan_tab_naming(
        self,
        *,
        document_id: FileId,
        title: str,
        operation_id: uuid.UUID,
        required_revision_id: DocsRevisionId,
        prefer_document_title: bool = False,
    ) -> None:
        del document_id, title, operation_id, required_revision_id, prefer_document_title
        self.naming_attempts += 1
        if self.naming_attempts == 1:
            raise DocumentGatewayError


@pytest.mark.parametrize("outage", ["planning", "applying"])
async def test_copy_retry_after_body_before_tab_naming_preserves_custom_header(
    monkeypatch: pytest.MonkeyPatch,
    outage: str,
) -> None:
    gateway = _OutageGateway(monkeypatch)
    tab_attempts: list[str] = []

    async def plan_tabs(**_kwargs: object) -> TabNamingCheckpoint:
        return TabNamingCheckpoint(
            plan=TabNamingPlan((), ()),
            revision_id=DocsRevisionId("revision-2"),
            marker_name="tabs",
            marker_range={"startIndex": 1, "endIndex": 2, "tabId": "tab-a"},
            invariant_sha256="test",
        )

    async def apply_tabs(**_kwargs: object) -> DocsRevisionId:
        tab_attempts.append("apply")
        if len(tab_attempts) == 1:
            raise DocumentGatewayError
        return DocsRevisionId("revision-2")

    if outage == "applying":
        monkeypatch.setattr(gateway, "plan_tab_naming", plan_tabs)
        monkeypatch.setattr(gateway, "apply_tab_naming", apply_tabs)
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    operation = uuid.uuid7()
    source = deepcopy(gateway.snapshot)
    custom = deepcopy(gateway.native["tabs"][0]["documentTab"]["headers"])
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="modified"
        ),
        mode=TransformMode.FORMAT,
    )
    with pytest.raises(DocumentGatewayError):
        await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
            operation_id=operation, request=request
        )
    assert store.checkpoints[-1].managed_headers == {}
    result = await _copy_executor(
        gateway=gateway, store=store, task_id=task_id, checkpoint=store.checkpoints[-1]
    ).execute(operation_id=operation, request=request)
    assert gateway.applies == 1
    assert len(set(gateway.body_digests)) == 1
    assert gateway.naming_attempts == (2 if outage == "planning" else 0)
    assert len(tab_attempts) == (2 if outage == "applying" else 0)
    assert result.needs_review
    assert any(finding.code == "header_conflict:tab-a" for finding in result.findings)
    assert gateway.native["tabs"][0]["documentTab"]["headers"] == custom
    assert gateway.snapshot == source
    assert store.resources == [FileId("output")]


@pytest.mark.parametrize("concurrent_edit", [False, True])
async def test_pre_body_recovery_uses_persisted_post_shell_revision(
    monkeypatch: pytest.MonkeyPatch, *, concurrent_edit: bool
) -> None:
    gateway = _OutageGateway(monkeypatch)
    gateway.naming_attempts = 1
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    operation = uuid.uuid7()
    request = DocumentTransformRequest(
        source=DriveFileSource(
            kind="drive_file", file_id=FileId("source"), expected_modified_time="modified"
        ),
        mode=TransformMode.FORMAT,
    )
    original_apply = gateway.apply_requests_once

    async def unavailable(**_kwargs: object) -> OutputDraft:
        raise DocumentGatewayError

    monkeypatch.setattr(gateway, "apply_requests_once", unavailable)
    with pytest.raises(DocumentGatewayError):
        await _copy_executor(gateway=gateway, store=store, task_id=task_id).execute(
            operation_id=operation, request=request
        )
    checkpoint = store.checkpoints[-1]
    assert checkpoint.managed_headers == {}
    assert checkpoint.output_revision_id == "revision-1"
    monkeypatch.setattr(gateway, "apply_requests_once", original_apply)
    if concurrent_edit:
        gateway.native["revisionId"] = "concurrent"
    retry = _copy_executor(gateway=gateway, store=store, task_id=task_id, checkpoint=checkpoint)
    if concurrent_edit:
        with pytest.raises(SourceChangedError):
            await retry.execute(operation_id=operation, request=request)
        assert gateway.applies == 0
    else:
        result = await retry.execute(operation_id=operation, request=request)
        assert result.needs_review
        assert gateway.applies == 1
