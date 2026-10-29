from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
import json
from typing import NamedTuple, override
import uuid

from pydantic import ValidationError
from pydantic_settings import BaseSettings
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.document_transform.schemas import DocumentTransformResult
from app.drive.schemas import MIME_DOC, MIME_FOLDER, MIME_SHORTCUT, DriveTree
from app.files.breadcrumb import build_breadcrumb
from app.files.errors import FileNotInScopeError
from app.files.service import GoogleFilesService
from app.identifiers import DriveId, FileId, IdempotencyKey, TaskId, UserId
from app.models.task import Task
from app.models.user import User
from app.smart_create.schemas import AIMode, BlankSource, SmartFileCreate
from app.smart_create.service import (
    ReorganizationEvidence,
    organization_candidates,
    smart_create_candidates,
)
from app.tasks.errors import TaskNotFoundError
from app.tasks.queue import CloudTasksEnqueuer
from app.tasks.schemas import TaskKind
from app.tasks.service import PostgresTaskService
from app.templates.service import DriveTemplateCatalogService
from tests.round7_provider_support import (
    PERSONAL,
    SHARED,
    DriveTransport,
    FolderId,
    JsonObject,
    ListPayload,
    ParentFolderId,
    RecordingTaskClient,
    deep_tree,
    fixture_user,
    folder,
    google_drive,
    manual_dispatch_body,
    owned_engine,
    sessions_for,
    shared_visited_catalog,
    shortcut_rows,
    shortcut_targets,
    streaming_canonical_hash,
    succeeded_task,
    wide_tree,
)
from tests.support import dependency


async def test_shortcut_duplicates_keep_target_binding_visibility_and_wire_identity() -> None:
    rows = shortcut_rows(80)
    rows.extend(
        [
            {"id": "no-target", "name": "Malformed shortcut", "mimeType": MIME_SHORTCUT},
            {"id": "ordinary", "name": "Ordinary", "mimeType": MIME_DOC},
        ]
    )
    transport = DriveTransport(children={"folder": rows}, targets=shortcut_targets())
    output = await google_drive(transport).list_folder_children(FileId("folder"))
    assert transport.batch_sizes == [8]
    assert len(output) == 61
    assert output[-1].id == "ordinary"
    by_id = {node.id: node for node in output}
    for index in range(80):
        node = by_id.get(FileId(f"shortcut-{index}"))
        if index % 8 in {3, 4}:
            assert node is None
        elif index % 8 == 2:
            assert node is not None
            assert node.shortcut_inaccessible
        else:
            assert node is not None
            assert not node.shortcut_inaccessible
            assert node.shortcut_target_drive_name == (
                SHARED.name if index % 8 in {0, 5, 7} else None
            )
    assert "no-target" not in by_id
    assert len([path for path, _params in transport.calls if "/files/target-" in path]) == 8


async def test_shortcut_work_is_principal_scoped_and_ordinary_rows_need_no_inventory() -> None:
    rows = shortcut_rows(8)
    denied = dict.fromkeys(shortcut_targets(), 403)
    one = await google_drive(
        DriveTransport(children={"folder": rows}, targets=denied)
    ).list_folder_children(FileId("folder"))
    two = await google_drive(
        DriveTransport(children={"folder": rows}, targets=shortcut_targets())
    ).list_folder_children(FileId("folder"))
    assert all(node.shortcut_inaccessible for node in one)
    assert not two[0].shortcut_inaccessible
    transport = DriveTransport(
        children={"folder": [{"id": "doc", "name": "Doc", "mimeType": MIME_DOC}]}
    )
    ordinary = await google_drive(transport).list_folder_children(FileId("folder"))
    assert ordinary[0].id == "doc"
    assert len(transport.calls) == 1
    assert transport.batch_sizes == []


def search_row(index: int, drive_id: DriveId | None = SHARED.id) -> dict[str, str]:
    row: dict[str, str] = {"id": f"hit-{index}", "name": f"Hit {index} 絵", "mimeType": MIME_DOC}
    if drive_id is not None:
        row["driveId"] = drive_id
    return row


@pytest.mark.parametrize("count", [0, 1, 20, 21, 50])
async def test_all_drive_completeness_keeps_per_drive_fallback_and_input_ownership(
    count: int,
) -> None:
    raw = [search_row(index) for index in range(count)]
    rows = dependency(raw)
    transport = DriveTransport(
        all_hits=rows, searches={SHARED.id: dependency([search_row(999)]), PERSONAL.id: []}
    )
    results = await google_drive(transport).search("document", limit_per_drive=20)
    assert [hit.id for hit in results] == (
        [f"hit-{index}" for index in range(count)] if count <= 20 else ["hit-999"]
    )
    assert all(hit.drive_name == SHARED.name for hit in results)
    assert all("drive_name" not in row for row in raw)
    shared_reads = [
        params for _path, params in transport.calls if params.get("corpora") == ["drive"]
    ]
    assert len(shared_reads) == (0 if count <= 20 else 1)
    assert all(params["supportsAllDrives"] == ["true"] for params in shared_reads)


def test_long_paths_keep_existing_depth_duplicate_cycle_and_unicode_semantics() -> None:
    tree = deep_tree(40)
    tree.folders.extend(
        [
            folder(FolderId("cycle-a"), "A", ParentFolderId("cycle-b")),
            folder(FolderId("cycle-b"), "B", ParentFolderId("cycle-a")),
            folder(FolderId("duplicate"), "Shallow Straße", None),
            folder(FolderId("duplicate"), "Deep Straße", ParentFolderId("level-8")),
            folder(FolderId("orphan"), "Orphan 日本語", ParentFolderId("absent")),
        ]
    )
    request = AIMode(
        kind="ai",
        description="level planning 日本語",
        file_type="document",
        invoking_drive_id=SHARED.id,
        invoking_folder_id=FileId("level-1"),
    )
    result = smart_create_candidates(request, [SHARED], [tree], candidate_depth=1)
    assert [item.folder_id for item in result] == [
        "level-1",
        "level-0",
        "level-2",
        "round7-shared",
        "cycle-b",
        "cycle-a",
        "orphan",
        "duplicate",
    ]
    assert result[-1].folder_path == [SHARED.name, "Shallow Straße"]
    with pytest.raises(ValueError, match="candidate_depth"):
        smart_create_candidates(request, [SHARED], [tree], candidate_depth=-1)


@pytest.mark.parametrize("depth", [1, 2, 63, 64, 65])
def test_breadcrumb_append_reverse_keeps_order_and_existing_depth_cap(depth: int) -> None:
    tree = DriveTree(
        drive_id=SHARED.id,
        folders=[
            folder(
                FolderId(f"level-{index}"),
                f"Level {index}",
                ParentFolderId(f"level-{index - 1}") if index else None,
            )
            for index in range(depth)
        ],
        files=[],
        files_complete=True,
    )
    result = build_breadcrumb(tree, SHARED.id, SHARED.name, FileId(f"level-{depth - 1}"))
    assert [item.id for item in result] == [
        SHARED.id,
        *(f"level-{index}" for index in range(max(0, depth - 64), depth)),
    ]


def test_breadcrumb_cycle_root_missing_and_absent_tree_contracts() -> None:
    tree = DriveTree(
        drive_id=SHARED.id,
        folders=[folder(FolderId("loop"), "Loop", ParentFolderId("loop"))],
        files=[],
        files_complete=True,
    )
    assert [item.id for item in build_breadcrumb(tree, SHARED.id, SHARED.name, FileId("loop"))] == [
        SHARED.id,
        *(["loop"] * 64),
    ]
    assert [
        item.id for item in build_breadcrumb(tree, SHARED.id, SHARED.name, FileId("missing"))
    ] == [FileId(SHARED.id)]
    assert build_breadcrumb(None, None, None, None) == []


class Database(NamedTuple):
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    user: User


class ProviderSettings(BaseSettings):
    wiki_round7_provider_database_url: str | None = None


@pytest.fixture
async def database() -> AsyncGenerator[Database]:
    url = ProviderSettings().wiki_round7_provider_database_url
    if url is None:
        pytest.skip("WIKI_ROUND7_PROVIDER_DATABASE_URL is not configured")
    engine = owned_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: User.metadata.create_all(
                sync, tables=[User.metadata.tables["users"], Task.metadata.tables["tasks"]]
            )
        )
    sessions = sessions_for(engine)
    user = fixture_user()
    async with sessions() as db:
        db.add(user)
        await db.commit()
    try:
        yield Database(engine, sessions, user)
    finally:
        async with sessions() as db:
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()
        await engine.dispose()


@pytest.mark.parametrize("evidence", [False, True])
async def test_task_copy_on_redaction_preserves_owner_qa_stripping_and_model_ownership(
    database: Database, evidence: bool
) -> None:
    task = succeeded_task(database.user, evidence=evidence)
    original = json.dumps(task.result, sort_keys=True)
    async with database.sessions() as db:
        db.add(task)
        await db.commit()
        service = PostgresTaskService(db=db)
        first = await service.get_for_owner(task.id, database.user.id)
        assert isinstance(first.result, DocumentTransformResult)
        assert "qa_permission_evidence" not in first.model_dump_json()
        first.result.output.name = "Owned response change"
        second = await service.get_for_owner(task.id, database.user.id)
        assert isinstance(second.result, DocumentTransformResult)
        assert second.result.output.name == "Output 絵"
        assert json.dumps(task.result, sort_keys=True) == original
        with pytest.raises(TaskNotFoundError):
            await service.get_for_owner(task.id, UserId(uuid.uuid7()))


async def test_rejected_json_validation_bypass_keeps_nonobject_payload_out_of_database(
    database: Database,
) -> None:
    payload = ListPayload()
    assert isinstance(payload.model_dump(mode="json"), list)
    with pytest.raises(ValidationError):
        JsonObject.model_validate(payload.model_dump(mode="json"))
    async with database.sessions() as db:
        with pytest.raises(ValidationError):
            await PostgresTaskService(db=db).create_or_replay(
                kind=TaskKind.FILE_SMART_CREATE,
                owner_user_id=database.user.id,
                idempotency_key=IdempotencyKey(uuid.uuid7()),
                payload=payload,
            )
        assert not db.in_transaction()


async def test_canonical_request_hash_matches_owned_stored_payload_and_streaming_control(
    database: Database,
) -> None:
    payload = SmartFileCreate(
        source=BlankSource(kind="blank", file_type="document"),
        parent_folder_id=FileId("parent"),
        name="Plan 絵 Straße",
        emoji="📋",
        alias="plan-draft",
    )
    canonical = JsonObject.model_validate(payload.model_dump(mode="json")).root
    async with database.sessions() as db:
        reserved = await PostgresTaskService(db=db).create_or_replay(
            kind=TaskKind.FILE_SMART_CREATE,
            owner_user_id=database.user.id,
            idempotency_key=IdempotencyKey(uuid.uuid7()),
            payload=payload,
        )
        stored = (
            await db.execute(select(Task.request_hash).where(Task.id == reserved.task.id))
        ).scalar_one()
        assert stored == streaming_canonical_hash(canonical)


class UntrustedTaskId:
    hex = "not-a-uuid"

    @override
    def __str__(self) -> str:
        return 'invalid"identifier'


async def test_rejected_manual_dispatch_keeps_uuid_validation_and_independent_proto_ownership() -> (
    None
):
    client = RecordingTaskClient()
    queue = CloudTasksEnqueuer(
        project="round7",
        location="local",
        queue="fixture",
        target_url="https://example.com/internal/tasks/smart-create",
        invoker_service_account="fixture@example.com",
        client=client,
    )
    malformed = UntrustedTaskId()
    with pytest.raises(ValidationError):
        await queue.enqueue_smart_create(dependency(malformed))
    assert client.requests == []
    with pytest.raises(json.JSONDecodeError):
        json.loads(manual_dispatch_body(malformed))
    first, second = TaskId(uuid.uuid7()), TaskId(uuid.uuid7())
    await queue.enqueue_smart_create(first)
    await queue.enqueue_smart_create(second)
    assert client.requests[0].task.http_request.body == manual_dispatch_body(first)
    assert client.requests[1].task.http_request.body == manual_dispatch_body(second)
    client.requests[0].task.http_request.headers["fixture-change"] = "one"
    assert "fixture-change" not in client.requests[1].task.http_request.headers
    await dependency(client.transport).close()


def template_graph() -> DriveTransport:
    return DriveTransport(
        children={
            SHARED.id: [
                {"id": "root-a", "name": "Templates", "mimeType": MIME_FOLDER},
                {"id": "root-b", "name": "Templates", "mimeType": MIME_FOLDER},
            ],
            "root-a": [{"id": "shared-category", "name": "Alpha", "mimeType": MIME_FOLDER}],
            "root-b": [{"id": "shared-category", "name": "Beta", "mimeType": MIME_FOLDER}],
            "shared-category": [
                {
                    "id": "template",
                    "name": "Template 絵",
                    "mimeType": MIME_DOC,
                    "modifiedTime": "2026-10-11T00:00:00Z",
                }
            ],
        }
    )


async def test_rejected_shared_template_visited_set_keeps_each_root_category_path() -> None:
    transport = template_graph()
    service = DriveTemplateCatalogService(drive=google_drive(transport), user_id=uuid.uuid7())
    actual = await service.list_templates(refresh=True)
    assert [template.category_path for template in actual.templates] == [["Alpha"], ["Beta"]]
    prototype_transport = template_graph()
    prototype = await shared_visited_catalog(google_drive(prototype_transport))
    assert [template.category_path for template in prototype] == [["Alpha"]]
    assert (
        len(
            [
                params
                for path, params in transport.calls
                if path.endswith("/files") and "shared-category" in params["q"][0]
            ]
        )
        == 2
    )
    assert (
        len(
            [
                params
                for path, params in prototype_transport.calls
                if path.endswith("/files") and "shared-category" in params["q"][0]
            ]
        )
        == 1
    )


def evidence() -> ReorganizationEvidence:
    return ReorganizationEvidence(
        file_name="Plan budget 日本語",
        description="Planning",
        content="Budget plan",
        modified_time=datetime.now(UTC).isoformat(),
    )


def test_streamed_reorganization_keeps_top80_current_ties_and_all_input_validation() -> None:
    tree = wide_tree(100)
    result = organization_candidates(evidence(), FileId("folder-99"), SHARED, tree)
    assert len(result) == 80
    assert result[0].folder_id == "folder-99"
    assert len({item.folder_id for item in result}) == 80
    tree.folders.append(folder(FolderId("bad-late"), "Invalid late record", None))
    tree.folders[-1].name = dependency(123)
    with pytest.raises(ValidationError):
        organization_candidates(evidence(), FileId("folder-99"), SHARED, tree)


async def test_public_files_client_factory_keeps_fresh_scope_and_requested_identity() -> None:
    user = fixture_user()
    transport = DriveTransport(
        targets={
            "doc": {"id": "doc", "name": "Doc", "mimeType": MIME_DOC, "driveId": SHARED.id},
            "mismatch": {"id": "other", "name": "Doc", "mimeType": MIME_DOC, "driveId": SHARED.id},
        }
    )
    service = GoogleFilesService(
        user=user, drive=google_drive(transport, user), client_factory=transport.factory
    )
    assert (await service.get(FileId("doc"))).id == "doc"
    with pytest.raises(FileNotInScopeError):
        await service.get(FileId("mismatch"))
    assert len([path for path, _params in transport.calls if path.endswith("/files/doc")]) == 1
