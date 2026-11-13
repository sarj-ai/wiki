from datetime import UTC, datetime
import hashlib
import json
from typing import Any, NamedTuple
import uuid

import pytest
from sqlalchemy.exc import NoResultFound

from app.document_transform.errors import OutputConflictError
from app.document_transform.header_provenance import (
    HeaderIdentityEvidence,
    HeaderIdentityFinalization,
    ManagedHeaderIdentity,
)
from app.document_transform.schemas import (
    CopyCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    MarkdownSource,
    TransformMode,
)
from app.identifiers import DocsSegmentId, DocsTabId, FileId, TaskId
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
        if self.value is None:
            raise NoResultFound
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
