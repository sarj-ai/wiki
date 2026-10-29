from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Any, Literal, Never, override
from unittest.mock import create_autospec
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google_tokens import GoogleTokenManager, ManagedGoogleToken
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.executor import PIPELINE_VERSION
from app.document_transform.gateway import SourceSnapshot
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.qa_contract import (
    PRODUCTION_QA_EXCHANGE_URL,
    PRODUCTION_WIKI_API_ORIGIN,
    QA_CORPUS_DOCUMENT_COUNT,
    QA_MAX_FOLDER_LIST_PAGES,
    QA_MAX_TRAVERSAL_METADATA_BYTES,
    QA_MAX_TRAVERSED_FOLDERS,
    required_document_count,
)
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformResult,
    DriveFileSource,
    TransformMode,
    ValidationSummary,
)
from app.identifiers import DocsRevisionId, DriveId, FileId, GoogleUserId, TaskId, UserId
from app.models.user import User
from scripts.qa_document_transform_corpus import (
    ArtifactFile,
    EvidenceManifest,
    EvidenceResult,
    LedgerRecord,
    PdfInfo,
    PermissionEntry,
    QaCohort,
    QaError,
    SourceEvidence,
    VisualEvidence,
    VisualJudgeEvidence,
    _append_ledger,
    _Arguments,
    _assert_private_destination,
    _capture_visual_evidence,
    _ensure_manifest_ledger,
    _evenly_sample,
    _inspect_candidate,
    _list_folder_children,
    _list_folder_doc_metadata,
    _load_ledger,
    _load_verified_canary,
    _manifest_plan_sha256,
    _parse_file_metadata,
    _parse_judge_response,
    _read_pdf_info,
    _render_pdf,
    _require_artifact_file,
    _require_copyable_source,
    _require_current_source_set,
    _require_manifest_invariants,
    _require_private_file,
    _require_private_permissions,
    _require_result_binding,
    _require_safe_semantic_delta,
    _require_transform_result,
    _run,
    _submit_transform,
    _submitted_tasks,
    _validate_arguments,
    _verified_results,
    _verify_release,
    _write_manifest,
    corpus_operation_key,
    permission_inventory_sha256,
    seeded_order,
)


if TYPE_CHECKING:
    from app.cache.service import CacheService


_EMAIL = "reviewer@example.com"


def test_seeded_order_is_stable_across_drive_page_order() -> None:
    forward = [{"id": value} for value in ("doc-a", "doc-b", "doc-c", "doc-d")]
    reverse = list(reversed(forward))

    assert [item["id"] for item in seeded_order(forward, "release-seed")] == [
        item["id"] for item in seeded_order(reverse, "release-seed")
    ]
    assert seeded_order(forward, "release-seed") != seeded_order(forward, "another-seed")


def test_shared_drive_folder_listing_uses_complete_shared_drive_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class Request:
        pass

    class Files:
        def list(self, **kwargs: object) -> Request:
            calls.append(kwargs)
            return Request()

    class Drive:
        @staticmethod
        def files() -> Files:
            return Files()

    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.execute_with_retry",
        lambda _request: {"files": [{"id": "doc-a"}]},
    )

    drive: Any = Drive()
    assert _list_folder_children(drive, FileId("folder-a"), shared_drive_id=DriveId("drive-a")) == [
        {"id": "doc-a"}
    ]
    assert calls == [
        {
            "q": "'folder-a' in parents and trashed=false",
            "spaces": "drive",
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
            "pageSize": 100,
            "pageToken": None,
            "fields": calls[0]["fields"],
            "corpora": "drive",
            "driveId": "drive-a",
        }
    ]


def test_folder_enumeration_recurses_and_returns_every_native_doc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = {
        "id": "root",
        "name": "Security",
        "mimeType": "application/vnd.google-apps.folder",
        "driveId": "drive-a",
    }
    pages = {
        "root": [
            {
                "id": "doc-a",
                "name": "A",
                "mimeType": "application/vnd.google-apps.document",
                "driveId": "drive-a",
            },
            {
                "id": "nested",
                "name": "Nested",
                "mimeType": "application/vnd.google-apps.folder",
                "driveId": "drive-a",
            },
            {
                "id": "sheet",
                "name": "Ignored",
                "mimeType": "application/vnd.google-apps.spreadsheet",
                "driveId": "drive-a",
            },
        ],
        "nested": [
            {
                "id": "doc-b",
                "name": "B",
                "mimeType": "application/vnd.google-apps.document",
                "driveId": "drive-a",
            }
        ],
    }
    seen: list[tuple[FileId, str | None]] = []
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._get_file_metadata",
        lambda _drive, _file_id: folder,
    )

    def children(
        _drive: object,
        folder_id: FileId,
        *,
        shared_drive_id: str | None,
        budget: object,
    ) -> list[dict[str, Any]]:
        del budget
        seen.append((folder_id, shared_drive_id))
        return pages[str(folder_id)]

    monkeypatch.setattr("scripts.qa_document_transform_corpus._list_folder_children", children)

    drive: Any = object()
    assert [item["id"] for item in _list_folder_doc_metadata(drive, FileId("root"))] == [
        "doc-a",
        "doc-b",
    ]
    assert seen == [(FileId("root"), "drive-a"), (FileId("nested"), "drive-a")]


def test_execution_revalidates_exact_complete_source_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _evidence_manifest().model_copy(
        update={
            "source_drive_id": "drive-a",
            "folder_source_ids": (FileId("doc-a"),),
            "folder_source_count": 1,
        }
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._get_file_metadata",
        lambda _drive, _file_id: {
            "id": "source-folder",
            "name": "Security",
            "mimeType": "application/vnd.google-apps.folder",
            "driveId": "drive-a",
        },
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._list_folder_doc_metadata",
        lambda _drive, _folder_id: [
            {
                "id": "doc-a",
                "mimeType": "application/vnd.google-apps.document",
                "driveId": "drive-a",
            }
        ],
    )

    drive: Any = object()
    _require_current_source_set(drive, manifest)

    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._list_folder_doc_metadata",
        lambda _drive, _folder_id: [
            {
                "id": "doc-a",
                "mimeType": "application/vnd.google-apps.document",
                "driveId": "drive-a",
            },
            {
                "id": "doc-added-after-plan",
                "mimeType": "application/vnd.google-apps.document",
                "driveId": "drive-a",
            },
        ],
    )
    with pytest.raises(QaError, match="complete source set changed"):
        _require_current_source_set(drive, manifest)


def test_qa_contract_has_exact_document_and_traversal_bounds() -> None:
    assert PRODUCTION_WIKI_API_ORIGIN == "https://api-wiki.sarj.ai"
    assert PRODUCTION_QA_EXCHANGE_URL == (
        "https://api-wiki.sarj.ai/internal/document-transform-qa/exchange"
    )
    assert required_document_count("canary") == 1
    assert required_document_count("corpus") == QA_CORPUS_DOCUMENT_COUNT == 20
    assert QA_MAX_TRAVERSED_FOLDERS == 100
    assert QA_MAX_FOLDER_LIST_PAGES == 100
    assert QA_MAX_TRAVERSAL_METADATA_BYTES == 4 * 1024 * 1024


def test_folder_enumeration_rejects_more_than_twenty_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._get_file_metadata",
        lambda _drive, _file_id: {
            "id": "root",
            "name": "Security",
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._list_folder_children",
        lambda _drive, _folder_id, **_kwargs: [
            {
                "id": f"doc-{index}",
                "name": f"Document {index}",
                "mimeType": "application/vnd.google-apps.document",
            }
            for index in range(21)
        ],
    )
    drive: Any = object()
    with pytest.raises(QaError, match="20-document corpus limit"):
        _list_folder_doc_metadata(drive, FileId("root"))


def test_folder_listing_enforces_aggregate_page_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page: dict[str, object] = {"files": [], "nextPageToken": "next"}
    last_page: dict[str, object] = {"files": []}
    responses: Iterator[dict[str, object]] = iter((first_page, last_page))
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.QA_MAX_FOLDER_LIST_PAGES",
        1,
    )
    request_count = 0

    def execute(_request: object) -> dict[str, object]:
        nonlocal request_count
        request_count += 1
        return next(responses)

    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.execute_with_retry",
        execute,
    )

    class Files:
        @staticmethod
        def list(**_kwargs: object) -> object:
            return object()

    class Drive:
        @staticmethod
        def files() -> Files:
            return Files()

    drive: Any = Drive()
    with pytest.raises(QaError, match="aggregate page limit"):
        _list_folder_children(drive, FileId("root"), shared_drive_id=None)
    assert request_count == 1


def test_folder_enumeration_shares_page_budget_across_nested_folders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._get_file_metadata",
        lambda _drive, _file_id: {
            "id": "root",
            "name": "Security",
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.QA_MAX_FOLDER_LIST_PAGES",
        2,
    )
    request_count = 0

    def execute(request: dict[str, object]) -> dict[str, object]:
        nonlocal request_count
        request_count += 1
        if "'root' in parents" in str(request["q"]):
            return {
                "files": [
                    {
                        "id": "nested",
                        "name": "Nested",
                        "mimeType": "application/vnd.google-apps.folder",
                    }
                ]
            }
        return {"files": [], "nextPageToken": "more"}

    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.execute_with_retry",
        execute,
    )

    class Files:
        @staticmethod
        def list(**kwargs: object) -> dict[str, object]:
            return kwargs

    class Drive:
        @staticmethod
        def files() -> Files:
            return Files()

    drive: Any = Drive()
    with pytest.raises(QaError, match="aggregate page limit"):
        _list_folder_doc_metadata(drive, FileId("root"))
    assert request_count == 2


def test_folder_enumeration_enforces_folder_and_metadata_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = {
        "id": "root",
        "name": "Security",
        "mimeType": "application/vnd.google-apps.folder",
    }
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._get_file_metadata",
        lambda _drive, _file_id: root,
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.QA_MAX_TRAVERSED_FOLDERS",
        1,
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._list_folder_children",
        lambda _drive, folder_id, **_kwargs: (
            [
                {
                    "id": "nested",
                    "name": "Nested",
                    "mimeType": "application/vnd.google-apps.folder",
                }
            ]
            if folder_id == "root"
            else []
        ),
    )
    drive: Any = object()
    with pytest.raises(QaError, match="bounded folder limit"):
        _list_folder_doc_metadata(drive, FileId("root"))

    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.QA_MAX_TRAVERSED_FOLDERS",
        QA_MAX_TRAVERSED_FOLDERS,
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.QA_MAX_TRAVERSAL_METADATA_BYTES",
        1,
    )
    with pytest.raises(QaError, match="aggregate metadata limit"):
        _list_folder_doc_metadata(drive, FileId("root"))


@pytest.mark.asyncio
async def test_shared_drive_copy_preflight_does_not_require_private_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = PermissionEntry(id="owner-1", principal_type="user", role="owner", email_address=_EMAIL)
    parent = {
        "id": "folder",
        "name": "Security",
        "mimeType": "application/vnd.google-apps.folder",
        "driveId": "drive-a",
        "ownedByMe": False,
        "shared": True,
        "capabilities": {"canEdit": False, "canAddChildren": False},
    }
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._get_file_metadata",
        lambda _drive, _file_id: parent,
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._permission_inventory",
        lambda _drive, _file_id: (owner,),
    )

    class CopyGateway:
        @staticmethod
        async def load_drive_source(source: DriveFileSource) -> SourceSnapshot:
            del source
            return SourceSnapshot(
                title="Existing custom header",
                parent_folder_id=FileId("folder"),
                modified_time="2026-09-03T00:00:00Z",
                revision_id=DocsRevisionId("revision-1"),
                media_type="application/vnd.google-apps.document",
                google_document={
                    "revisionId": "revision-1",
                    "tabs": [
                        {
                            "tabProperties": {"tabId": "tab-1"},
                            "documentTab": {
                                "body": {
                                    "content": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 6,
                                            "paragraph": {
                                                "elements": [{"textRun": {"content": "Text\n"}}]
                                            },
                                        }
                                    ]
                                },
                                "headers": {"custom-header": {"content": []}},
                            },
                        }
                    ],
                },
            )

        @staticmethod
        async def inspect_in_place(**_kwargs: object) -> Never:
            raise AssertionError

    drive_client: Any = object()
    evidence = await _inspect_candidate(
        raw={
            "id": "doc-a",
            "name": "Policy",
            "mimeType": "application/vnd.google-apps.document",
            "parents": ["folder"],
            "modifiedTime": "2026-09-03T00:00:00Z",
            "driveId": "drive-a",
            "ownedByMe": False,
            "shared": True,
            "capabilities": {"canEdit": False, "canCopy": True},
        },
        ordinal=1,
        drive_client=drive_client,
        gateway=CopyGateway(),
        expected_release_sha="a" * 40,
        seed="folder-copy",
        destination_folder_id=FileId("destination"),
        source_drive_id=DriveId("drive-a"),
        mode=TransformMode.BOTH,
        user_email=_EMAIL,
        cohort="corpus",
    )

    assert evidence.source_id == FileId("doc-a")
    assert evidence.drive_id == "drive-a"


def test_uuid5_operation_key_is_stable_and_scoped() -> None:
    first = corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id=FileId("doc-a"),
        destination_folder_id=FileId("destination"),
        mode=TransformMode.BOTH,
        cohort="corpus",
    )

    assert first == corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id=FileId("doc-a"),
        destination_folder_id=FileId("destination"),
        mode=TransformMode.BOTH,
        cohort="corpus",
    )
    assert first.version == 5
    assert first != corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id=FileId("doc-b"),
        destination_folder_id=FileId("destination"),
        mode=TransformMode.BOTH,
        cohort="corpus",
    )
    assert first != corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id=FileId("doc-a"),
        destination_folder_id=FileId("destination"),
        mode=TransformMode.BOTH,
        cohort="canary",
    )
    assert first != corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id=FileId("doc-a"),
        destination_folder_id=FileId("another-destination"),
        mode=TransformMode.BOTH,
        cohort="corpus",
    )


def test_shared_drive_source_must_remain_copyable_and_in_the_bound_drive() -> None:
    metadata = _parse_file_metadata(
        {
            "id": "doc-a",
            "name": "Policy",
            "mimeType": "application/vnd.google-apps.document",
            "driveId": "drive-a",
            "capabilities": {"canCopy": True},
        }
    )
    _require_copyable_source(metadata, expected_drive_id=DriveId("drive-a"))

    with pytest.raises(QaError, match="source_not_copyable"):
        _require_copyable_source(
            metadata.model_copy(update={"can_copy": False}),
            expected_drive_id=DriveId("drive-a"),
        )
    with pytest.raises(QaError, match="source_drive_changed"):
        _require_copyable_source(metadata, expected_drive_id=DriveId("drive-b"))


def test_live_execution_is_hard_gated_to_all_folder_copies() -> None:
    common = replace(
        _canary_arguments(),
        canary=False,
        confirm_live_copy="CREATE_ALL_OWNER_ONLY_DESTINATION_COPIES",
        count=20,
        source_folder_id="security-folder",
        canary_manifest=Path("canary.json"),
        canary_ledger=Path("canary.jsonl"),
        canary_artifacts_dir=Path("canary-artifacts"),
    )
    _validate_arguments(common)

    with pytest.raises(QaError, match="must equal 20 for corpus"):
        _validate_arguments(replace(common, count=19))
    with pytest.raises(QaError, match="source-folder-id"):
        _validate_arguments(replace(common, source_folder_id=""))
    with pytest.raises(QaError, match="requires --mode both"):
        _validate_arguments(replace(common, mode=TransformMode.FORMAT))
    with pytest.raises(QaError, match="exact pinned model"):
        _validate_arguments(replace(common, judge_model="gemini-2.5-flash"))
    with pytest.raises(QaError, match="canary-artifacts-dir"):
        _validate_arguments(replace(common, canary_artifacts_dir=None))


def test_canary_execution_has_distinct_one_document_gate() -> None:
    common = _canary_arguments()
    _validate_arguments(common)

    with pytest.raises(QaError, match="CREATE_1_PRIVATE_CANARY"):
        _validate_arguments(
            replace(common, confirm_live_copy="CREATE_ALL_OWNER_ONLY_DESTINATION_COPIES")
        )
    with pytest.raises(QaError, match="must equal 1 for canary"):
        _validate_arguments(replace(common, count=20))
    _validate_arguments(
        replace(
            common,
            artifacts_dir=None,
            confirm_live_copy=None,
            count=1,
            execute=False,
            pdftoppm_version=None,
            judge_model=None,
        )
    )


def test_private_selection_requires_owned_unshared_editable_file_and_owner() -> None:
    metadata = _parse_file_metadata(
        {
            "id": "doc-a",
            "name": "Private",
            "mimeType": "application/vnd.google-apps.document",
            "parents": ["folder-a"],
            "modifiedTime": "2026-09-03T00:00:00Z",
            "ownedByMe": True,
            "shared": False,
            "capabilities": {"canEdit": True, "canAddChildren": False},
            "owners": [{"emailAddress": _EMAIL}],
        }
    )
    _require_private_file(
        metadata,
        _EMAIL,
        expected_mime="application/vnd.google-apps.document",
    )

    with pytest.raises(QaError, match="not_private_my_drive"):
        _require_private_file(
            metadata.model_copy(update={"shared": True}),
            _EMAIL,
            expected_mime="application/vnd.google-apps.document",
        )


def test_private_permission_inventory_is_owner_only_and_digest_is_canonical() -> None:
    owner = PermissionEntry(
        id="owner-1",
        principal_type="user",
        role="owner",
        email_address=_EMAIL,
    )
    _require_private_permissions((owner,), _EMAIL)

    assert permission_inventory_sha256((owner,)) == permission_inventory_sha256((owner,))
    with pytest.raises(QaError, match="not_owner_only_permissions"):
        _require_private_permissions(
            (
                owner,
                PermissionEntry(id="domain", principal_type="domain", role="reader"),
            ),
            _EMAIL,
        )


@pytest.mark.asyncio
async def test_destination_must_be_owner_only_writable_my_drive_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = PermissionEntry(
        id="owner-1",
        principal_type="user",
        role="owner",
        email_address=_EMAIL,
    )
    destination = {
        "id": "destination",
        "name": "Wiki Polish Security QA",
        "mimeType": "application/vnd.google-apps.folder",
        "ownedByMe": True,
        "shared": False,
        "capabilities": {"canAddChildren": True},
        "owners": [{"emailAddress": _EMAIL}],
    }
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._get_file_metadata",
        lambda _drive, _file_id: destination,
    )
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._permission_inventory",
        lambda _drive, _file_id: (owner,),
    )
    drive: Any = object()

    assert await _assert_private_destination(
        drive,
        FileId("destination"),
        _EMAIL,
    ) == (owner,)

    destination["driveId"] = "shared-drive"
    with pytest.raises(QaError, match="not_private_my_drive"):
        await _assert_private_destination(drive, FileId("destination"), _EMAIL)


def test_semantic_postcondition_accepts_candidate_only_deletion() -> None:
    before = DocumentIR(
        title="Before",
        blocks=(TextBlock(id="b0", kind=BlockKind.PARAGRAPH, text="Moreover, we reviewed."),),
        source_format="google_doc",
    )
    after = before.replace_blocks({"b0": "We reviewed."})

    _require_safe_semantic_delta(before, after, mode=TransformMode.BOTH)


def test_semantic_postcondition_rejects_format_text_change_and_unsafe_rewrite() -> None:
    before = DocumentIR(
        title="Before",
        blocks=(TextBlock(id="b0", kind=BlockKind.PARAGRAPH, text="Moreover, we approved."),),
        source_format="google_doc",
    )

    with pytest.raises(QaError, match="format-only"):
        _require_safe_semantic_delta(
            before,
            before.replace_blocks({"b0": "We approved."}),
            mode=TransformMode.FORMAT,
        )
    with pytest.raises(QaError, match="validated unslop-only"):
        _require_safe_semantic_delta(
            before,
            before.replace_blocks({"b0": "We rejected."}),
            mode=TransformMode.BOTH,
        )


def test_exact_transform_result_gate_rejects_wrong_brand_digest() -> None:
    task_id = TaskId(uuid.uuid7())
    result = DocumentTransformResult(
        operation_id=task_id,
        source_sha256="source",
        output=DocumentOutput(
            file_id=FileId("output"),
            name="Output",
            parent_folder_id=FileId("folder"),
        ),
        mode=TransformMode.BOTH,
        applied_patch_count=1,
        findings=[],
        validation=ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=True,
            structure_preserved=True,
            unsafe_patch_count=0,
        ),
        model_ids=[],
        tool_versions={},
        pipeline_version=PIPELINE_VERSION,
        needs_review=False,
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        deterministic_patch_count=1,
        model_patch_count=0,
        llm_invoked=False,
    )
    _require_transform_result(result, task_id=task_id, mode=TransformMode.BOTH)

    with pytest.raises(QaError, match="exact transform telemetry"):
        _require_transform_result(
            result.model_copy(update={"brand_profile_sha256": "wrong"}),
            task_id=task_id,
            mode=TransformMode.BOTH,
        )


def test_jsonl_ledger_resumes_submitted_and_verified_operations(tmp_path: Path) -> None:
    operation_key = uuid.uuid5(uuid.NAMESPACE_URL, "operation")
    task_id = TaskId(uuid.uuid7())
    result = EvidenceResult(
        source_id=FileId("source"),
        source_modified_time_after="2026-09-03T00:00:00Z",
        source_revision_id_after="revision-1",
        source_semantic_sha256_after="semantic",
        source_permission_inventory_after=(),
        source_permission_sha256_after=permission_inventory_sha256(()),
        task_id=task_id,
        output_id=FileId("output"),
        output_parent_id=FileId("folder"),
        output_owned_by_me=True,
        output_shared=False,
        output_revision_id="revision-2",
        output_permission_inventory=(),
        output_permission_sha256=permission_inventory_sha256(()),
        applied_patch_count=0,
        deterministic_patch_count=0,
        model_patch_count=0,
        llm_invoked=False,
        release_sha_before="a" * 40,
        release_sha_after="a" * 40,
        visual_evidence=_visual_evidence(),
        verified_at=datetime.now(UTC),
    )
    records = (
        _submitted_ledger_record(
            operation_key=operation_key,
            source_id=FileId("source"),
            task_id=task_id,
        ),
        _verified_ledger_record(
            operation_key=operation_key,
            source_id=FileId("source"),
            result=result,
        ),
    )
    path = tmp_path / "qa.jsonl"
    path.write_text(
        "".join(f"{record.model_dump_json()}\n" for record in records),
        encoding="utf-8",
    )

    loaded = _load_ledger(path)

    assert _submitted_tasks(loaded) == {operation_key: task_id}
    assert _verified_results(loaded) == {operation_key: result}


def test_jsonl_ledger_rejects_truncated_record(tmp_path: Path) -> None:
    path = tmp_path / "qa.jsonl"
    path.write_text(json.dumps({"schema_version": 1})[:-1], encoding="utf-8")

    with pytest.raises(QaError, match="invalid ledger record at line 1"):
        _load_ledger(path)


def test_ledger_is_bound_to_immutable_manifest_plan(tmp_path: Path) -> None:
    source = _source_evidence()
    manifest = _evidence_manifest(
        seed="seed-a",
        source=source,
    )
    path = tmp_path / "qa.jsonl"

    first = _ensure_manifest_ledger(path, manifest)
    second = _ensure_manifest_ledger(path, manifest)

    assert first == second
    assert [record.event for record in first] == ["manifest"]
    with pytest.raises(QaError, match="does not belong"):
        _ensure_manifest_ledger(path, manifest.model_copy(update={"seed": "seed-b"}))


def test_verified_canary_attestation_is_bound_to_release_and_ledger(tmp_path: Path) -> None:
    expected_release_sha = "a" * 40
    judge_model = "gemini-3.8-flash"
    seed = "canary-seed"
    source = _source_evidence(
        expected_release_sha=expected_release_sha,
        seed=seed,
        cohort="canary",
    )
    artifacts_dir = tmp_path / "artifacts"
    visual_evidence = _visual_evidence(source=source, artifacts_dir=artifacts_dir)
    result = _evidence_result(
        expected_release_sha=expected_release_sha,
        source=source,
        visual_evidence=visual_evidence,
        judge_model=judge_model,
    )
    manifest = _evidence_manifest(
        expected_release_sha=expected_release_sha,
        judge_model=judge_model,
        seed=seed,
        source=source,
        results=(result,),
    )
    manifest_path = tmp_path / "canary.json"
    ledger_path = tmp_path / "canary.jsonl"
    _write_manifest(manifest_path, manifest)
    _append_ledger(
        ledger_path,
        _manifest_ledger_record(_manifest_plan_sha256(manifest)),
    )
    _append_ledger(
        ledger_path,
        _verified_ledger_record(
            operation_key=source.operation_key,
            source_id=source.source_id,
            result=result,
        ),
    )

    attestation = _load_verified_canary(
        manifest_path,
        ledger_path,
        artifacts_dir,
        expected_release_sha=expected_release_sha,
        user_email=_EMAIL,
        judge_model=judge_model,
    )

    assert len(attestation.sha256) == 64
    assert attestation.excluded_source_ids == {source.source_id}
    with pytest.raises(QaError, match="exact release"):
        _load_verified_canary(
            manifest_path,
            ledger_path,
            artifacts_dir,
            expected_release_sha="b" * 40,
            user_email=_EMAIL,
            judge_model=judge_model,
        )
    format_manifest = manifest.model_copy(update={"mode": TransformMode.FORMAT})
    _write_manifest(manifest_path, format_manifest)
    ledger_path.unlink()
    _append_ledger(
        ledger_path,
        _manifest_ledger_record(_manifest_plan_sha256(format_manifest)),
    )
    _append_ledger(
        ledger_path,
        _verified_ledger_record(
            operation_key=source.operation_key,
            source_id=source.source_id,
            result=result,
        ),
    )
    with pytest.raises(QaError, match="exact release"):
        _load_verified_canary(
            manifest_path,
            ledger_path,
            artifacts_dir,
            expected_release_sha=expected_release_sha,
            user_email=_EMAIL,
            judge_model=judge_model,
        )


def test_private_evidence_files_use_restrictive_modes(tmp_path: Path) -> None:
    manifest = _evidence_manifest()
    manifest_path = tmp_path / "nested" / "manifest.json"
    ledger_path = tmp_path / "nested" / "ledger.jsonl"

    _write_manifest(manifest_path, manifest)
    _append_ledger(
        ledger_path,
        _manifest_ledger_record("digest"),
    )

    assert manifest_path.stat().st_mode & 0o777 == 0o600
    assert ledger_path.stat().st_mode & 0o777 == 0o600
    assert manifest_path.parent.stat().st_mode & 0o777 == 0o700


def test_preexisting_permissive_evidence_directory_is_rejected(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    unsafe.chmod(0o755)

    with pytest.raises(QaError, match="already be private"):
        _append_ledger(
            unsafe / "ledger.jsonl",
            _manifest_ledger_record("digest"),
        )


def test_manifest_invariants_reject_tampered_source_binding() -> None:
    release = "a" * 40
    seed = "seed"
    source = _source_evidence(expected_release_sha=release, seed=seed, cohort="canary")
    manifest = _evidence_manifest(
        expected_release_sha=release,
        seed=seed,
        source=source,
    )

    _require_manifest_invariants(manifest)
    with pytest.raises(QaError, match="structural invariants"):
        _require_manifest_invariants(
            manifest.model_copy(update={"folder_source_ids": (FileId("different"),)})
        )
    with pytest.raises(QaError, match="source binding"):
        _require_manifest_invariants(
            manifest.model_copy(
                update={"sources": (source.model_copy(update={"output_name": "tampered"}),)}
            )
        )


def test_resumed_result_requires_intact_private_artifacts(tmp_path: Path) -> None:
    release = "a" * 40
    source = _source_evidence(expected_release_sha=release)
    artifacts_dir = tmp_path / "artifacts"
    evidence = _visual_evidence(source=source, artifacts_dir=artifacts_dir)
    result = _evidence_result(
        expected_release_sha=release,
        source=source,
        visual_evidence=evidence,
    )

    _require_result_binding(
        source,
        result,
        destination_folder_id=FileId("destination"),
        expected_release_sha=release,
        artifacts_dir=artifacts_dir,
        renderer_version="25.06.0",
        judge_model=None,
        user_email=_EMAIL,
    )
    with pytest.raises(QaError, match="bound to its source"):
        _require_result_binding(
            source,
            result.model_copy(update={"release_sha_after": "b" * 40}),
            destination_folder_id=FileId("destination"),
            expected_release_sha=release,
            artifacts_dir=artifacts_dir,
            renderer_version="25.06.0",
            judge_model=None,
            user_email=_EMAIL,
        )
    incomplete_evidence = evidence.model_copy(update={"source_screenshots": ()})
    with pytest.raises(QaError, match="bound to its source"):
        _require_result_binding(
            source,
            result.model_copy(update={"visual_evidence": incomplete_evidence}),
            destination_folder_id=FileId("destination"),
            expected_release_sha=release,
            artifacts_dir=artifacts_dir,
            renderer_version="25.06.0",
            judge_model=None,
            user_email=_EMAIL,
        )
    output_png = artifacts_dir / evidence.output_screenshots[0].relative_path
    output_png.write_bytes(b"corrupt")
    output_png.chmod(0o600)
    with pytest.raises(QaError, match="size or digest changed"):
        _require_result_binding(
            source,
            result,
            destination_folder_id=FileId("destination"),
            expected_release_sha=release,
            artifacts_dir=artifacts_dir,
            renderer_version="25.06.0",
            judge_model=None,
            user_email=_EMAIL,
        )


def test_artifact_paths_cannot_escape_private_root(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir(mode=0o700)
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"secret")
    secret.chmod(0o600)
    artifact = ArtifactFile(
        relative_path="../secret.png",
        sha256=hashlib.sha256(b"secret").hexdigest(),
        byte_size=6,
    )

    with pytest.raises(QaError, match="outside"):
        _require_artifact_file(artifact, artifacts_dir=root)


def test_ledger_rejects_failure_after_verified_result() -> None:
    operation_key = uuid.uuid5(uuid.NAMESPACE_URL, "verified-then-failed")
    result = _evidence_result(expected_release_sha="a" * 40)
    records = (
        _verified_ledger_record(
            operation_key=operation_key,
            source_id=result.source_id,
            result=result,
        ),
        _failed_ledger_record(
            operation_key=operation_key,
            detail="late_failure",
        ),
    )

    with pytest.raises(QaError, match="failure after"):
        _verified_results(records)


def test_even_sampling_includes_first_and_last_pages() -> None:
    artifacts = tuple(
        ArtifactFile(relative_path=f"page-{index}.png", sha256=str(index) * 64, byte_size=1)
        for index in range(10)
    )

    sampled = _evenly_sample(artifacts, 4)

    assert len(sampled) == 4
    assert sampled[0] == artifacts[0]
    assert sampled[-1] == artifacts[-1]


@pytest.mark.parametrize(
    ("pdfinfo_output", "error"),
    [
        ("Pages:          101\nPage size:      612 x 792 pts\n", "page count"),
        ("Pages:          1\nPage size:      3000 x 792 pts\n", "dimensions"),
    ],
)
def test_pdf_preflight_rejects_unbounded_rendering(
    monkeypatch: pytest.MonkeyPatch,
    pdfinfo_output: str,
    error: str,
) -> None:
    monkeypatch.setattr("scripts.qa_document_transform_corpus.shutil.which", lambda _: "/pdfinfo")
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["pdfinfo"],
            returncode=0,
            stdout=pdfinfo_output,
            stderr="",
        ),
    )

    with pytest.raises(QaError, match=error):
        _read_pdf_info(Path("private.pdf"))


def test_render_rejects_incomplete_or_oversized_screenshot_sets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("scripts.qa_document_transform_corpus.shutil.which", lambda _: "/pdftoppm")
    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._read_pdf_info",
        lambda _path: PdfInfo(pages=2, page_sizes_points=((612.0, 792.0),)),
    )

    def render_one_page(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(f"{command[-1]}-1.png").write_bytes(b"png")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("scripts.qa_document_transform_corpus.subprocess.run", render_one_page)
    with pytest.raises(QaError, match="exactly one screenshot"):
        _render_pdf(tmp_path / "source.pdf", capture_dir=tmp_path, stem="source")

    def render_two_pages(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(f"{command[-1]}-1.png").write_bytes(b"png")
        Path(f"{command[-1]}-2.png").write_bytes(b"png")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("scripts.qa_document_transform_corpus.subprocess.run", render_two_pages)
    monkeypatch.setattr("scripts.qa_document_transform_corpus._MAX_RENDERED_BYTES", 1)
    with pytest.raises(QaError, match="aggregate evidence"):
        _render_pdf(tmp_path / "source.pdf", capture_dir=tmp_path, stem="source")


def test_judge_response_is_exact_and_redacts_invalid_payload() -> None:
    secret = "PRIVATE_DOCUMENT_SENTINEL"
    payload = json.dumps(
        {
            "verdict": "pass",
            "comparison": "improved",
            "typography_score": 5,
            "spacing_score": 5,
            "hierarchy_score": 5,
            "issue_codes": [],
            "extra_private_text": secret,
        }
    )

    with pytest.raises(QaError) as captured:
        _parse_judge_response(None, payload)
    assert secret not in str(captured.value)
    assert "strict validation" in str(captured.value)


@pytest.mark.asyncio
async def test_release_fence_returns_observed_sha_and_rejects_drift() -> None:
    expected = "a" * 40

    def respond(request: httpx.Request) -> httpx.Response:
        revision = expected if request.headers.get("X-Test") is None else "b" * 40
        return httpx.Response(
            status_code=httpx.codes.OK,
            json={"status": "ok", "database": "ok", "revision": revision},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await _verify_release(client, expected) == expected
        client.headers["X-Test"] = "drift"
        with pytest.raises(QaError, match="expected release"):
            await _verify_release(client, expected)


@pytest.mark.asyncio
async def test_visual_judge_rejects_regression_even_with_high_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _visual_evidence()

    async def judge(*_args: object, **_kwargs: object) -> VisualJudgeEvidence:
        return VisualJudgeEvidence(
            model_id="gemini-pinned",
            verdict="pass",
            comparison="regressed",
            typography_score=5,
            spacing_score=5,
            hierarchy_score=5,
            judged_screenshot_sha256=("a" * 64,),
        )

    monkeypatch.setattr(
        "scripts.qa_document_transform_corpus._capture_visual_evidence_sync",
        lambda **_kwargs: evidence,
    )
    monkeypatch.setattr("scripts.qa_document_transform_corpus._judge_visual_evidence", judge)

    with pytest.raises(QaError, match="requested review"):
        await _capture_visual_evidence(
            source=_source_evidence(),
            source_id=FileId("source"),
            output_id=FileId("output"),
            drive_client=create_autospec(object),
            artifacts_dir=Path("artifacts"),
            renderer_version="25.06.0",
            judge_model="gemini-pinned",
        )


@pytest.mark.asyncio
async def test_submission_uses_live_endpoint_and_uuid5_idempotency_header() -> None:
    source = _source_evidence()
    operation_key = source.operation_key
    task_id = uuid.uuid7()

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api-wiki.sarj.ai/document-transforms"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Idempotency-Key"] == str(operation_key)
        assert json.loads(request.content)["source"]["file_id"] == "source"
        return httpx.Response(
            status_code=httpx.codes.ACCEPTED,
            headers={"Idempotency-Replayed": "false"},
            json={
                "id": str(task_id),
                "kind": "file.document_transform",
                "created_at": "2026-09-03T00:00:00Z",
                "status": "pending",
                "result": None,
                "resource_id": None,
                "error_code": None,
                "error_detail": None,
                "attempt_count": 0,
                "started_at": None,
                "finished_at": None,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        task, replayed = await _submit_transform(
            client,
            "test-token",
            TransformMode.BOTH,
            source,
            destination_folder_id=FileId("destination"),
        )

    assert task.id == task_id
    assert replayed is False


def _canary_arguments() -> _Arguments:
    return _Arguments(
        seed="qa",
        manifest=Path("manifest.json"),
        ledger=Path("ledger.jsonl"),
        canary=True,
        confirm_live_copy="CREATE_1_PRIVATE_CANARY",
        count=1,
        user_email=_EMAIL,
        source_folder_id="canary-folder",
        destination_folder_id="qa-output-folder",
        execute=True,
        expected_release_sha="a" * 40,
        mode=TransformMode.BOTH,
        artifacts_dir=Path("artifacts"),
        pdftoppm_version="25.06.0",
        judge_model="gemini-3.8-flash",
    )


def _evidence_manifest(
    *,
    expected_release_sha: str = "a" * 40,
    seed: str = "seed",
    source: SourceEvidence | None = None,
    results: tuple[EvidenceResult, ...] = (),
    judge_model: str | None = None,
) -> EvidenceManifest:
    source = source or _source_evidence(
        expected_release_sha=expected_release_sha,
        seed=seed,
        cohort="canary",
    )
    return EvidenceManifest(
        created_at=datetime.now(UTC),
        expected_release_sha=expected_release_sha,
        cohort="canary",
        judge_model=judge_model,
        seed=seed,
        seed_sha256=hashlib.sha256(seed.encode()).hexdigest(),
        mode=TransformMode.BOTH,
        source_folder_id=FileId("folder"),
        source_drive_id=None,
        destination_folder_id=FileId("destination"),
        destination_permission_inventory=source.permission_inventory,
        destination_permission_sha256=source.permission_sha256,
        folder_source_ids=(source.source_id,),
        folder_source_count=1,
        count=1,
        user_email=_EMAIL,
        pipeline_version=PIPELINE_VERSION,
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        selection_rejections={},
        sources=(source,),
        results=results,
    )


def _manifest_ledger_record(detail: str) -> LedgerRecord:
    return _ledger_record(
        event="manifest",
        detail=detail,
    )


def _submitted_ledger_record(
    *,
    operation_key: uuid.UUID,
    source_id: FileId,
    task_id: TaskId,
) -> LedgerRecord:
    return _ledger_record(
        event="submitted",
        operation_key=operation_key,
        source_id=source_id,
        task_id=task_id,
    )


def _verified_ledger_record(
    *,
    operation_key: uuid.UUID,
    source_id: FileId,
    result: EvidenceResult,
) -> LedgerRecord:
    return _ledger_record(
        event="verified",
        operation_key=operation_key,
        source_id=source_id,
        task_id=result.task_id,
        output_id=result.output_id,
        result=result,
    )


def _failed_ledger_record(*, operation_key: uuid.UUID, detail: str) -> LedgerRecord:
    return _ledger_record(event="failed", operation_key=operation_key, detail=detail)


def _ledger_record(
    *,
    event: Literal["manifest", "submitted", "terminal", "verified", "failed"],
    operation_key: uuid.UUID | None = None,
    source_id: FileId | None = None,
    task_id: TaskId | None = None,
    output_id: FileId | None = None,
    detail: str | None = None,
    result: EvidenceResult | None = None,
) -> LedgerRecord:
    return LedgerRecord(
        recorded_at=datetime.now(UTC),
        event=event,
        operation_key=operation_key,
        source_id=source_id,
        task_id=task_id,
        output_id=output_id,
        detail=detail,
        result=result,
    )


def _source_evidence(
    *,
    expected_release_sha: str = "a" * 40,
    seed: str = "seed",
    cohort: QaCohort = "canary",
) -> SourceEvidence:
    owner = PermissionEntry(
        id="owner-1",
        principal_type="user",
        role="owner",
        email_address=_EMAIL,
    )
    operation_key = corpus_operation_key(
        expected_release_sha=expected_release_sha,
        seed=seed,
        source_id=FileId("source"),
        destination_folder_id=FileId("destination"),
        mode=TransformMode.BOTH,
        cohort=cohort,
    )
    return SourceEvidence(
        ordinal=1,
        source_id=FileId("source"),
        drive_id=None,
        parent_id=FileId("folder"),
        modified_time="2026-09-03T00:00:00Z",
        revision_id="revision-1",
        semantic_sha256="semantic",
        permission_inventory=(owner,),
        permission_sha256=permission_inventory_sha256((owner,)),
        parent_permission_inventory=(owner,),
        parent_permission_sha256=permission_inventory_sha256((owner,)),
        operation_key=operation_key,
        output_name=(
            f"[QA {'CANARY' if cohort == 'canary' else 'CORPUS'} "
            f"{expected_release_sha[:12]} {hashlib.sha256(seed.encode()).hexdigest()[:8]} 01] "
            "Sarj polished"
        ),
    )


def _visual_evidence(
    *,
    source: SourceEvidence | None = None,
    artifacts_dir: Path | None = None,
) -> VisualEvidence:
    source = source or _source_evidence()
    prefix = f"{source.ordinal:02d}-{source.operation_key}"

    def artifact(name: str, content: bytes) -> ArtifactFile:
        if artifacts_dir is not None:
            artifacts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            artifacts_dir.chmod(0o700)
            path = artifacts_dir / prefix / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.parent.chmod(0o700)
            path.write_bytes(content)
            path.chmod(0o600)
        return ArtifactFile(
            relative_path=f"{prefix}/{name}",
            sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
        )

    return VisualEvidence(
        source_pdf=artifact("source.pdf", b"source-pdf"),
        output_pdf=artifact("output.pdf", b"output-pdf"),
        source_screenshots=(artifact("source-001.png", b"source-png"),),
        output_screenshots=(artifact("output-001.png", b"output-png"),),
        renderer_version="25.06.0",
    )


def _evidence_result(
    *,
    expected_release_sha: str,
    source: SourceEvidence | None = None,
    visual_evidence: VisualEvidence | None = None,
    judge_model: str | None = None,
) -> EvidenceResult:
    source = source or _source_evidence(expected_release_sha=expected_release_sha)
    evidence = visual_evidence or _visual_evidence(source=source)
    judge = None
    if judge_model is not None:
        judge = VisualJudgeEvidence(
            model_id=judge_model,
            verdict="pass",
            comparison="improved",
            typography_score=5,
            spacing_score=5,
            hierarchy_score=5,
            judged_screenshot_sha256=tuple(
                item.sha256 for item in (*evidence.source_screenshots, *evidence.output_screenshots)
            ),
        )
    return EvidenceResult(
        source_id=source.source_id,
        source_modified_time_after=source.modified_time,
        source_revision_id_after=source.revision_id,
        source_semantic_sha256_after=source.semantic_sha256,
        source_permission_inventory_after=source.permission_inventory,
        source_permission_sha256_after=source.permission_sha256,
        task_id=TaskId(uuid.uuid7()),
        output_id=FileId("output"),
        output_parent_id=FileId("destination"),
        output_owned_by_me=True,
        output_shared=False,
        output_revision_id="revision-2",
        output_permission_inventory=source.permission_inventory,
        output_permission_sha256=source.permission_sha256,
        applied_patch_count=0,
        deterministic_patch_count=0,
        model_patch_count=0,
        llm_invoked=False,
        release_sha_before=expected_release_sha,
        release_sha_after=expected_release_sha,
        visual_evidence=evidence,
        visual_judge=judge,
        verified_at=datetime.now(UTC),
    )


async def test_run_awaits_cache_factory_and_preserves_failure_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected_user = User(
        id=UserId(uuid.uuid7()), google_id=GoogleUserId("cache-fixture"), email="cache@example.test"
    )
    token = ManagedGoogleToken(
        "cache-fixture-token", datetime.now(UTC) + timedelta(hours=1), 1, "fixture-ciphertext"
    )
    events: list[str] = []
    clients: list[httpx.AsyncClient] = []
    expected_failure = RuntimeError("cache provider unavailable")

    async def selected_user(_db: AsyncSession, user_email: str) -> User:
        assert user_email == expected_user.email
        events.append("selected-user")
        return expected_user

    class PreparedTokens(GoogleTokenManager):
        @override
        async def ensure_valid(
            self, user: User, *, force_generation: int | None = None
        ) -> ManagedGoogleToken:
            assert user is expected_user
            assert force_generation is None
            events.append("prepared-user")
            self._apply_to_user(user, token)
            return token

    async def failed_cache_factory() -> CacheService:
        events.append("awaited-cache")
        raise expected_failure

    def unexpected_http(_request: httpx.Request) -> httpx.Response:
        message = "cache failure must stop setup before any HTTP request"
        raise AssertionError(message)

    client_type = httpx.AsyncClient

    def private_client(*, timeout: float) -> httpx.AsyncClient:
        client = client_type(timeout=timeout, transport=httpx.MockTransport(unexpected_http))
        clients.append(client)
        return client

    monkeypatch.setenv("WIKI_QA_ACCESS_TOKEN", "cache-fixture-token")
    monkeypatch.delenv("CLOUD_RUN_JOB", raising=False)
    monkeypatch.delenv("WIKI_QA_DURABLE_ARTIFACT_ROOT", raising=False)
    arguments = replace(
        _canary_arguments(),
        manifest=tmp_path / "manifest.json",
        ledger=tmp_path / "ledger.jsonl",
        artifacts_dir=tmp_path / "artifacts",
        user_email=expected_user.email,
    )
    with pytest.raises(RuntimeError, match=r"^cache provider unavailable$") as failure:
        await _run(
            arguments,
            cache_factory=failed_cache_factory,
            token_manager_factory=PreparedTokens,
            user_reader=selected_user,
            http_client_factory=private_client,
        )
    assert failure.value is expected_failure
    assert events == ["selected-user", "prepared-user", "awaited-cache"]
    assert len(clients) == 1
    assert clients[0].is_closed
