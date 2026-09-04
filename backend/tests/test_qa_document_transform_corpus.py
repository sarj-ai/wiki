from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import uuid

import httpx
import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.executor import PIPELINE_VERSION
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformResult,
    TransformMode,
    ValidationSummary,
)
from app.identifiers import FileId, TaskId
from scripts.qa_document_transform_corpus import (
    EvidenceManifest,
    EvidenceResult,
    LedgerRecord,
    PermissionEntry,
    QaError,
    SourceEvidence,
    _ensure_manifest_ledger,
    _load_ledger,
    _parse_file_metadata,
    _require_private_file,
    _require_private_permissions,
    _require_safe_semantic_delta,
    _require_transform_result,
    _submit_transform,
    _submitted_tasks,
    _validate_arguments,
    _verified_results,
    corpus_operation_key,
    permission_inventory_sha256,
    seeded_order,
)


_EMAIL = "nasr@sarj.ai"


def test_seeded_order_is_stable_across_drive_page_order() -> None:
    forward = [{"id": value} for value in ("doc-a", "doc-b", "doc-c", "doc-d")]
    reverse = list(reversed(forward))

    assert [item["id"] for item in seeded_order(forward, "release-seed")] == [
        item["id"] for item in seeded_order(reverse, "release-seed")
    ]
    assert seeded_order(forward, "release-seed") != seeded_order(forward, "another-seed")


def test_uuid5_operation_key_is_stable_and_scoped() -> None:
    first = corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id="doc-a",
        mode=TransformMode.BOTH,
    )

    assert first == corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id="doc-a",
        mode=TransformMode.BOTH,
    )
    assert first.version == 5
    assert first != corpus_operation_key(
        expected_release_sha="a" * 40,
        seed="corpus-1",
        source_id="doc-b",
        mode=TransformMode.BOTH,
    )


def test_live_execution_is_hard_gated_to_twenty_both_mode_copies() -> None:
    common = {
        "canary": False,
        "confirm_live_copy": "CREATE_20_PRIVATE_SIBLINGS",
        "count": 20,
        "execute": True,
        "expected_release_sha": "a" * 40,
        "mode": "both",
    }
    _validate_arguments(argparse.Namespace(**common))

    with pytest.raises(QaError, match="requires --count 20"):
        _validate_arguments(argparse.Namespace(**{**common, "count": 19}))
    with pytest.raises(QaError, match="requires --mode both"):
        _validate_arguments(argparse.Namespace(**{**common, "mode": "format"}))


def test_canary_execution_has_distinct_one_document_gate() -> None:
    common = {
        "canary": True,
        "confirm_live_copy": "CREATE_1_PRIVATE_CANARY",
        "count": 1,
        "execute": True,
        "expected_release_sha": "a" * 40,
        "mode": "both",
    }
    _validate_arguments(argparse.Namespace(**common))

    with pytest.raises(QaError, match="CREATE_1_PRIVATE_CANARY"):
        _validate_arguments(
            argparse.Namespace(**{**common, "confirm_live_copy": "CREATE_20_PRIVATE_SIBLINGS"})
        )
    with pytest.raises(QaError, match="requires --count 1"):
        _validate_arguments(argparse.Namespace(**{**common, "count": 20}))
    with pytest.raises(QaError, match="only valid with --execute"):
        _validate_arguments(
            argparse.Namespace(
                **{
                    **common,
                    "confirm_live_copy": None,
                    "count": 1,
                    "execute": False,
                }
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


def test_exact_v2_result_gate_rejects_wrong_brand_digest() -> None:
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

    with pytest.raises(QaError, match="exact v2 telemetry"):
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
        verified_at=datetime.now(UTC),
    )
    records = (
        LedgerRecord(
            recorded_at=datetime.now(UTC),
            event="submitted",
            operation_key=operation_key,
            source_id=FileId("source"),
            task_id=task_id,
        ),
        LedgerRecord(
            recorded_at=datetime.now(UTC),
            event="verified",
            operation_key=operation_key,
            source_id=FileId("source"),
            task_id=task_id,
            output_id=FileId("output"),
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
    manifest = EvidenceManifest(
        created_at=datetime.now(UTC),
        expected_release_sha="a" * 40,
        seed="seed-a",
        seed_sha256="seed-digest",
        mode=TransformMode.BOTH,
        count=1,
        user_email=_EMAIL,
        pipeline_version=PIPELINE_VERSION,
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        selection_rejections={},
        sources=(source,),
    )
    path = tmp_path / "qa.jsonl"

    first = _ensure_manifest_ledger(path, manifest)
    second = _ensure_manifest_ledger(path, manifest)

    assert first == second
    assert [record.event for record in first] == ["manifest"]
    with pytest.raises(QaError, match="does not belong"):
        _ensure_manifest_ledger(path, manifest.model_copy(update={"seed": "seed-b"}))


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
        task, replayed = await _submit_transform(client, "test-token", TransformMode.BOTH, source)

    assert task.id == task_id
    assert replayed is False


def _source_evidence() -> SourceEvidence:
    return SourceEvidence(
        ordinal=1,
        source_id=FileId("source"),
        parent_id=FileId("folder"),
        modified_time="2026-09-03T00:00:00Z",
        revision_id="revision-1",
        semantic_sha256="semantic",
        permission_inventory=(),
        permission_sha256=permission_inventory_sha256(()),
        parent_permission_inventory=(),
        parent_permission_sha256=permission_inventory_sha256(()),
        operation_key=uuid.uuid5(uuid.NAMESPACE_URL, "operation"),
        output_name="[QA] Sarj polished",
    )
