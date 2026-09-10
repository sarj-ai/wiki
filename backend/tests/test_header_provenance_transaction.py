from datetime import UTC, datetime
import hashlib
import json
from typing import Any, NamedTuple
import uuid

import pytest

from app.document_transform.errors import OutputConflictError
from app.document_transform.header_provenance import (
    HeaderIdentityEvidence,
    HeaderIdentityFinalization,
    ManagedHeaderIdentity,
)
from app.document_transform.schemas import (
    CopyCheckpoint,
    CopyCheckpointStage,
    DocumentTransformRequest,
    DocumentTransformResult,
    MarkdownSource,
    QaCopyPermissionEvidence,
    QaDestinationPermissionBinding,
    QaDocumentTransformResult,
    QaFilePermissionEvidence,
    TransformMode,
)
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId, TaskId
from app.models.task import Task
from app.models.wiki import DocumentHeaderProvenance
from app.tasks import service as service_module
from app.tasks.errors import TaskLeaseLostError
from app.tasks.schemas import TaskKind, TaskStatus
from app.tasks.service import PostgresTaskService
from tests.test_document_transform import _copy_executor, _CopyCheckpointStore, _Gateway
from tests.test_tasks import _task


class _Row:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value

    def scalar_one(self) -> Any:
        return self.value


class _Transaction:
    def __init__(self, rows: list[object]) -> None:
        self.rows = iter(rows)
        self.statements: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object) -> _Row:
        self.statements.append(statement)
        return _Row(next(self.rows))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _checkpoint_payload(checkpoint: CopyCheckpoint) -> dict[str, object]:
    return dict(service_module._serialize_checkpoint(checkpoint))


class PreparedProvenance(NamedTuple):
    task: Task
    result: DocumentTransformResult
    row: DocumentHeaderProvenance


async def prepared_provenance(scenario: str) -> PreparedProvenance:
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="Decision",
            name="Decision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
    )
    result = await _copy_executor(gateway=_Gateway(), store=store, task_id=task_id).execute(
        operation_id=task_id, request=request
    )
    evidence = HeaderIdentityEvidence(
        operation_id=task_id,
        file_id=result.output.file_id,
        profile_sha256=result.brand_profile_sha256,
        title_sha256="a" * 64,
        tab_ids=(DocsTabId("tab-a"),),
        headers=(
            ManagedHeaderIdentity(
                tab_id=DocsTabId("tab-a"),
                header_id=DocsSegmentId("header-a"),
                fingerprint_sha256="b" * 64,
            ),
        ),
    )
    finalized = result.model_copy(update={"header_identity": evidence})
    digest = hashlib.sha256(
        json.dumps(
            finalized.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert finalized.output_revision_id is not None
    revision_digest = hashlib.sha256(finalized.output_revision_id.encode()).hexdigest()
    checkpoint = store.checkpoints[-1].model_copy(
        update={
            "result": finalized,
            "managed_headers": {"tab-a": "header-a"},
            "header_finalization": HeaderIdentityFinalization(
                result_sha256=digest, verified_revision_sha256=revision_digest
            ),
        }
    )
    if scenario == "unfinalized":
        checkpoint = checkpoint.model_copy(update={"header_finalization": None})
    elif scenario == "unmanaged":
        checkpoint = checkpoint.model_copy(update={"managed_headers": {}})
    aliased = finalized.model_copy(
        update={"short_link_alias": "header-proof", "short_link_status": "ready"}
    )
    task = _task(request_hash="request", status=TaskStatus.SUCCEEDED)
    task.id = task_id
    task.kind = TaskKind.FILE_DOCUMENT_TRANSFORM
    task.attempt_count = 1
    task.started_at = datetime.now(UTC)
    task.result = aliased.model_dump(mode="json")
    task.finished_at = datetime.now(UTC)
    task.checkpoint = _checkpoint_payload(checkpoint)
    row = DocumentHeaderProvenance(
        owner_user_id=task.owner_user_id,
        file_id=evidence.file_id,
        producer_operation_id=task_id,
        evidence=evidence.model_dump(mode="json"),
        result_sha256="c" * 64 if scenario == "conflict" else digest,
        verified_revision_sha256=revision_digest,
    )
    return PreparedProvenance(task, aliased, row)


@pytest.mark.parametrize(
    "scenario", ["success", "lost_lease", "conflict", "unfinalized", "unmanaged"]
)
async def test_provenance_is_atomic_with_owned_task_completion(scenario: str) -> None:
    task, aliased, row = await prepared_provenance(scenario)
    transaction = _Transaction([None] if scenario == "lost_lease" else [task, None, row])
    session: Any = transaction
    service = PostgresTaskService(db=session)
    if scenario == "success":
        await service.succeed(task.id, lease_token=uuid.uuid7(), result=aliased)
        assert transaction.commits == 1
        assert transaction.rollbacks == 0
        assert len(transaction.statements) == 3
        assert (
            row.result_sha256
            != hashlib.sha256(
                json.dumps(
                    aliased.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
    else:
        with pytest.raises(TaskLeaseLostError if scenario == "lost_lease" else OutputConflictError):
            await service.succeed(task.id, lease_token=uuid.uuid7(), result=aliased)
        assert transaction.commits == 0
        assert transaction.rollbacks == 1


def _qa_permission_evidence(
    task: Task, result: DocumentTransformResult
) -> QaCopyPermissionEvidence:
    assert result.output_revision_id is not None
    observed_at = datetime.now(UTC)
    return QaCopyPermissionEvidence(
        operation_id=task.id,
        source_sha256=result.source_sha256,
        output_revision_id=result.output_revision_id,
        destination=QaFilePermissionEvidence(
            file_id=FileId("folder"),
            mime_type="application/vnd.google-apps.folder",
            parent_folder_id=None,
            version="17",
            permission_sha256="d" * 64,
            observed_at=observed_at,
        ),
        output=QaFilePermissionEvidence(
            file_id=result.output.file_id,
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("folder"),
            version="19",
            permission_sha256="e" * 64,
            observed_at=observed_at,
        ),
    )


async def _prepared_qa_provenance(scenario: str) -> PreparedProvenance:
    task = (await prepared_provenance("success")).task
    assert task.checkpoint is not None
    checkpoint = service_module._deserialize_checkpoint(task.checkpoint)
    assert isinstance(checkpoint, CopyCheckpoint)
    assert checkpoint.result is not None
    header_identity = checkpoint.result.header_identity
    assert header_identity is not None
    output_revision_id = checkpoint.result.output_revision_id
    assert output_revision_id is not None
    permission_evidence = _qa_permission_evidence(task, checkpoint.result)
    qa_result = QaDocumentTransformResult.model_validate(
        checkpoint.result.model_dump(mode="python")
        | {
            "access_scope": "owner_only",
            "qa_permission_evidence": permission_evidence,
        }
    )
    if scenario == "preexisting_link_change":
        qa_result = qa_result.model_copy(
            update={"short_link_alias": "original-link", "short_link_status": "ready"}
        )
    result_digest = hashlib.sha256(
        json.dumps(
            service_module._public_document_transform_payload(qa_result),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    revision_digest = hashlib.sha256(output_revision_id.encode()).hexdigest()
    qa_checkpoint = CopyCheckpoint.model_validate(
        checkpoint.model_dump(mode="python")
        | {
            "stage": CopyCheckpointStage.ATTESTED,
            "qa_destination_permission": QaDestinationPermissionBinding(
                version="17", sha256="f" * 64
            ),
            "qa_permission_evidence": permission_evidence,
            "result": qa_result,
            "header_finalization": HeaderIdentityFinalization(
                result_sha256=result_digest,
                verified_revision_sha256=revision_digest,
            ),
        }
    )
    if scenario == "wrong_stage":
        qa_checkpoint = qa_checkpoint.model_copy(update={"stage": CopyCheckpointStage.VERIFIED})
    elif scenario == "evidence_mismatch":
        mismatched = permission_evidence.model_copy(
            update={
                "output": permission_evidence.output.model_copy(
                    update={"permission_sha256": "0" * 64}
                )
            }
        )
        qa_checkpoint = qa_checkpoint.model_copy(update={"qa_permission_evidence": mismatched})
    link_update: dict[str, object] = {
        "short_link_alias": "qa-header-proof",
        "short_link_status": "ready",
    }
    if scenario == "unavailable_success":
        link_update = {"short_link_alias": None, "short_link_status": "unavailable"}
    elif scenario == "alias_unavailable":
        link_update["short_link_status"] = "unavailable"
    elif scenario == "none_ready":
        link_update["short_link_alias"] = None
    aliased = qa_result.model_copy(update=link_update)
    task.result = aliased.model_dump(mode="json")
    task.checkpoint = _checkpoint_payload(qa_checkpoint)
    row = DocumentHeaderProvenance(
        owner_user_id=task.owner_user_id,
        file_id=header_identity.file_id,
        producer_operation_id=task.id,
        evidence=header_identity.model_dump(mode="json"),
        result_sha256=result_digest,
        verified_revision_sha256=revision_digest,
    )
    return PreparedProvenance(task, aliased, row)


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "unavailable_success",
        "wrong_stage",
        "evidence_mismatch",
        "submitted_plain",
        "submitted_evidence_mismatch",
        "invalid_ciphertext",
        "alias_unavailable",
        "none_ready",
        "preexisting_link_change",
    ],
)
async def test_qa_attested_provenance_requires_exact_terminal_acl_evidence(scenario: str) -> None:
    task, result, row = await _prepared_qa_provenance(scenario)
    if scenario == "submitted_plain":
        result = DocumentTransformResult.model_validate(
            service_module._public_document_transform_payload(result)
        )
    elif scenario == "submitted_evidence_mismatch":
        assert isinstance(result, QaDocumentTransformResult)
        result = result.model_copy(
            update={
                "qa_permission_evidence": result.qa_permission_evidence.model_copy(
                    update={
                        "output": result.qa_permission_evidence.output.model_copy(
                            update={"permission_sha256": "0" * 64}
                        )
                    }
                )
            }
        )
    elif scenario == "invalid_ciphertext":
        task.checkpoint = {
            "kind": "copy",
            "version": 1,
            "checkpoint_ciphertext": "not-a-fernet-token",
        }
    transaction = _Transaction([task, None, row])
    session: Any = transaction

    if scenario in {"success", "unavailable_success"}:
        public = await PostgresTaskService(db=session).succeed(
            task.id, lease_token=uuid.uuid7(), result=result
        )
        assert transaction.commits == 1
        assert transaction.rollbacks == 0
        assert public.result is not None
        assert "qa_permission_evidence" not in public.result.model_dump(mode="json")
    else:
        with pytest.raises(OutputConflictError):
            await PostgresTaskService(db=session).succeed(
                task.id, lease_token=uuid.uuid7(), result=result
            )
        assert transaction.commits == 0
        assert transaction.rollbacks == 1


@pytest.mark.parametrize(
    "mutation",
    ["operation_id", "source_sha256", "output_revision_id", "mode", "validation"],
)
async def test_qa_attested_provenance_rejects_other_submitted_terminal_payload_drift(
    mutation: str,
) -> None:
    task, result, row = await _prepared_qa_provenance("success")
    assert isinstance(result, QaDocumentTransformResult)
    match mutation:
        case "operation_id":
            result = result.model_copy(update={"operation_id": uuid.uuid7()})
        case "source_sha256":
            result = result.model_copy(update={"source_sha256": "0" * 64})
        case "output_revision_id":
            result = result.model_copy(
                update={"output_revision_id": DocsRevisionId("different-revision")}
            )
        case "mode":
            result = result.model_copy(update={"mode": TransformMode.UNSLOP})
        case _:
            result = result.model_copy(
                update={
                    "validation": result.validation.model_copy(
                        update={"structure_preserved": False}
                    )
                }
            )
    transaction = _Transaction([task, None, row])
    session: Any = transaction

    with pytest.raises(OutputConflictError):
        await PostgresTaskService(db=session).succeed(
            task.id, lease_token=uuid.uuid7(), result=result
        )

    assert transaction.commits == 0
    assert transaction.rollbacks == 1
