from __future__ import annotations

from collections import Counter
import json
import random

from pydantic import ValidationError
import pytest

from app.auth.encryption import decrypt_cache, encrypt_cache
from app.document_transform.errors import CheckpointTooLargeError
from app.document_transform.schemas import (
    CopyCheckpoint,
    DocumentTransformCheckpoint,
    InPlaceCheckpoint,
    WordCopyCheckpoint,
)
from app.drive.schemas import DriveTree
from app.identifiers import FileId
from app.smart_create.schemas import ProposalDestination
from app.smart_create.service import (
    GeminiSmartProposalService,
    ReorganizationEvidence,
    VertexProposalGenerator,
    VertexReorganizationGenerator,
    organization_candidates,
    smart_create_candidates,
)
from app.tasks.schemas import TaskKind, TaskStatus
from app.tasks.service import PostgresTaskService
from app.templates.schemas import TemplateRead
from tests.advanced_round6_support import (
    FIXTURE_DRIVE,
    FIXTURE_LEASE,
    FIXTURE_TASK,
    FIXTURE_TIME,
    FixtureDrive,
    FixtureGenaiClient,
    FixtureProposalGenerator,
    FixtureSession,
    FixtureSidebarCache,
    FixtureTemplates,
    ai_request,
    fixture_dependency,
    folder_node,
    generated_proposal,
    in_place_checkpoint,
    smart_payload,
    task_row,
    verified_copy_checkpoint,
    word_checkpoint,
)


@pytest.mark.parametrize("reorganization", [False, True])
async def test_public_vertex_fixture_receives_exact_unicode_prompt_context(
    reorganization: bool,
) -> None:
    folders = [
        ProposalDestination(
            folder_id=FileId(f"folder-{index}"),
            drive_id=FIXTURE_DRIVE.id,
            drive_name=FIXTURE_DRIVE.name,
            folder_path=[FIXTURE_DRIVE.name, f'Category 📚 "{index}"'],
        )
        for index in range(80)
    ]
    if reorganization:
        fixture = FixtureGenaiClient('{"destination_folder_id":"folder-0","reason":"Fits."}')
        evidence = ReorganizationEvidence(
            content="Private fixture content 絵\n" * 300,
            description=None,
            file_name="Record 📚",
            modified_time=None,
        )
        reorganization_result = await VertexReorganizationGenerator(
            client=fixture_dependency(fixture)
        ).generate(current=folders[0], evidence=evidence, folders=folders)
        assert reorganization_result.destination_folder_id == "folder-0"
        expected = {
            "current_destination": folders[0].model_dump(mode="json"),
            "document": evidence.model_dump(mode="json"),
            "folders": [folder.model_dump(mode="json") for folder in folders],
        }
    else:
        fixture = FixtureGenaiClient(
            '{"name":"Record","emoji":"📝","alias":"project-decision",'
            '"destination_folder_id":"folder-0"}'
        )
        request = ai_request("Project decision record 絵\n" * 50)
        templates = [
            TemplateRead(
                file_id=FileId(f"template-{index}"),
                drive_id=FIXTURE_DRIVE.id,
                drive_name=FIXTURE_DRIVE.name,
                name=f"Template 絵 {index}",
                description=None,
                mime_type="application/vnd.google-apps.document",
                modified_time="2026-10-11T00:00:00Z",
                category_path=["Category", str(index)],
            )
            for index in range(80)
        ]
        result = await VertexProposalGenerator(client=fixture_dependency(fixture)).generate(
            request, folders, templates
        )
        assert result.destination_folder_id == "folder-0"
        expected = {
            "description": request.description,
            "file_type": request.file_type,
            "folders": [folder.model_dump(mode="json") for folder in folders],
            "templates": [template.model_dump(mode="json") for template in templates],
        }
    context = fixture.models.prompt.split("\n", 1)[1]
    assert context == json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    assert fixture.models.calls == 1


def test_folder_paths_preserve_cycles_missing_parents_and_duplicate_identifiers() -> None:
    rng = random.Random(6061011)
    evidence = ReorganizationEvidence(
        content="", description=None, file_name="Project", modified_time=None
    )
    for _ in range(30):
        folders = [
            folder_node(
                FileId(f"folder-{index}"),
                f"Name {rng.randrange(8)}",
                None if rng.randrange(6) == 0 else FileId(f"folder-{rng.randrange(35)}"),
            )
            for index in range(30)
        ]
        folders.append(folder_node(FileId("folder-4"), "Duplicate canonical", FileId("folder-12")))
        rng.shuffle(folders)
        tree = DriveTree(drive_id=FIXTURE_DRIVE.id, folders=folders, files=[], files_complete=True)
        by_id = {folder.id: folder for folder in folders}
        expected: Counter[tuple[FileId, tuple[str, ...]]] = Counter()
        for folder in folders:
            names = [folder.name]
            seen = {folder.id}
            parent_id = folder.parent_id
            while parent_id is not None and parent_id in by_id and parent_id not in seen:
                seen.add(parent_id)
                parent = by_id[parent_id]
                names.append(parent.name)
                parent_id = parent.parent_id
            expected[folder.id, (FIXTURE_DRIVE.name, *reversed(names))] += 1
        candidates = organization_candidates(evidence, None, FIXTURE_DRIVE, tree)
        actual = Counter(
            (candidate.folder_id, tuple(candidate.folder_path))
            for candidate in candidates
            if candidate.folder_id != FileId(FIXTURE_DRIVE.id)
        )
        assert actual == expected


def test_candidate_depth_retains_invoking_ancestry_before_constructing_destinations() -> None:
    tree = DriveTree(
        drive_id=FIXTURE_DRIVE.id,
        folders=[
            folder_node(FileId("root-folder"), "Group", None),
            folder_node(FileId("middle"), "Section", FileId("root-folder")),
            folder_node(FileId("current"), "Current", FileId("middle")),
            folder_node(FileId("child"), "Child", FileId("current")),
            folder_node(FileId("deep"), "Deep", FileId("child")),
            folder_node(FileId("other"), "Other", None),
        ],
        files=[],
        files_complete=True,
    )
    request = ai_request()
    request.invoking_folder_id = FileId("current")
    bounded = smart_create_candidates(request, [FIXTURE_DRIVE], [tree], candidate_depth=0)
    assert {candidate.folder_id for candidate in bounded} == {
        FIXTURE_DRIVE.id,
        "root-folder",
        "middle",
        "current",
        "other",
    }
    bounded[0].folder_path.append("Mutated caller path")
    tree.folders[0].name = "Renamed group"
    fresh = smart_create_candidates(request, [FIXTURE_DRIVE], [tree], candidate_depth=0)
    assert all("Mutated caller path" not in candidate.folder_path for candidate in fresh)
    current = next(candidate for candidate in fresh if candidate.folder_id == "current")
    assert current.folder_path == [FIXTURE_DRIVE.name, "Renamed group", "Section", "Current"]
    all_candidates = smart_create_candidates(request, [FIXTURE_DRIVE], [tree])
    assert len(all_candidates) == 7


@pytest.mark.parametrize(
    ("folder_name", "description", "accepted"),
    [
        ("Projects", "Project decision record", True),
        ("Finance", "Finance budget record", True),
        ("Finance", "Project decision record", False),
        ("Health", "Doctor appointment record", True),
        ("Health", "Plant growth project record", False),
    ],
)
async def test_sensitive_destination_gate_retains_explicit_evidence(
    folder_name: str,
    description: str,
    accepted: bool,
) -> None:
    tree = DriveTree(
        drive_id=FIXTURE_DRIVE.id,
        folders=[folder_node(FileId("selected"), folder_name, None)],
        files=[],
        files_complete=True,
    )
    session = FixtureSession()
    service = GeminiSmartProposalService(
        db=fixture_dependency(session),
        drive=FixtureDrive(),
        sidebar_cache=FixtureSidebarCache(tree),
        templates=FixtureTemplates(),
        generator=FixtureProposalGenerator(generated_proposal(FileId("selected"))),
    )
    result = await service.propose(ai_request(description))
    assert (result.destination.folder_id == "selected") is accepted


@pytest.mark.parametrize("smart", [False, True])
async def test_worker_envelope_validates_status_invariants_once(smart: bool) -> None:
    row = task_row(TaskKind.FILE_SMART_CREATE if smart else TaskKind.FILE_DOCUMENT_TRANSFORM)
    payload: dict[str, object] = (
        smart_payload().model_dump(mode="json")
        if smart
        else {
            "source": {
                "kind": "drive_file",
                "file_id": "source-1",
                "expected_modified_time": "now",
            },
            "mode": "format",
        }
    )
    row.payload = payload
    session = FixtureSession(row)
    service = PostgresTaskService(db=fixture_dependency(session))
    record = await (
        service.load_smart_create(row.id) if smart else service.load_document_transform(row.id)
    )
    assert record.task.status is TaskStatus.PENDING
    assert record.owner_user_id == row.owner_user_id
    row.attempt_count = 2
    session = FixtureSession(row)
    service = PostgresTaskService(db=fixture_dependency(session))
    with pytest.raises(ValidationError):
        await (
            service.load_smart_create(row.id) if smart else service.load_document_transform(row.id)
        )


async def test_transform_worker_preserves_terminal_redaction_and_private_evidence_filter() -> None:
    checkpoint = verified_copy_checkpoint()
    assert checkpoint.result is not None
    row = task_row(TaskKind.FILE_DOCUMENT_TRANSFORM)
    row.status = TaskStatus.SUCCEEDED
    row.attempt_count = 1
    row.started_at = FIXTURE_TIME
    row.finished_at = FIXTURE_TIME
    redacted: dict[str, object] = {"redacted": True}
    row.payload = redacted
    result_data: dict[str, object] = checkpoint.result.model_dump(mode="json")
    result_data["qa_permission_evidence"] = {"private": "must not leave storage"}
    row.result = result_data
    session = FixtureSession(row)
    record = await PostgresTaskService(db=fixture_dependency(session)).load_document_transform(
        row.id
    )
    assert record.payload is None
    assert record.task.result is not None
    assert "qa_permission_evidence" not in record.task.result.model_dump(mode="json")


@pytest.mark.parametrize("kind", ["in_place", "copy", "word_copy"])
async def test_checkpoint_storage_restores_sorted_legacy_and_current_envelopes(kind: str) -> None:
    checkpoint: DocumentTransformCheckpoint
    if kind == "in_place":
        checkpoint = in_place_checkpoint(request_count=4)
    elif kind == "copy":
        checkpoint = verified_copy_checkpoint()
    else:
        checkpoint = word_checkpoint()
    session = FixtureSession(1)
    generation = await PostgresTaskService(
        db=fixture_dependency(session)
    ).save_document_transform_checkpoint(
        FIXTURE_TASK, lease_token=FIXTURE_LEASE, expected_generation=0, checkpoint=checkpoint
    )
    assert generation == 1
    statement = session.statements[0]
    saved = statement.compile().params["checkpoint"]
    assert "lease_token IS NOT DISTINCT FROM" in str(statement)
    assert "checkpoint_generation =" in str(statement)
    dumped = checkpoint.model_dump(mode="json")
    legacy: dict[str, object]
    if isinstance(checkpoint, InPlaceCheckpoint):
        plan_fields = {"source_title", "requests", "replacements", "findings", "headers"}
        expected = {key: value for key, value in dumped.items() if key in plan_fields}
        ciphertext_key = "plan_ciphertext"
        legacy = {key: value for key, value in dumped.items() if key not in plan_fields}
    else:
        assert isinstance(checkpoint, CopyCheckpoint | WordCopyCheckpoint)
        expected = dumped
        ciphertext_key = "checkpoint_ciphertext"
        legacy = {"kind": checkpoint.kind, "version": 1}
    assert json.loads(decrypt_cache(saved[ciphertext_key])) == expected
    assert "Decision 絵" not in json.dumps(saved)
    legacy[ciphertext_key] = encrypt_cache(
        json.dumps(expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )
    for stored in (legacy, saved):
        row = task_row(TaskKind.FILE_DOCUMENT_TRANSFORM)
        row.checkpoint = stored
        payload: dict[str, object] = {
            "source": {
                "kind": "drive_file",
                "file_id": "source-1",
                "expected_modified_time": "now",
            },
            "mode": "format",
        }
        row.payload = payload
        db = FixtureSession(row)
        restored = await PostgresTaskService(db=fixture_dependency(db)).load_document_transform(
            row.id
        )
        assert restored.checkpoint == checkpoint


async def test_checkpoint_exact_utf8_cap_and_pre_sql_rejection() -> None:
    checkpoint = in_place_checkpoint(title="")
    serialized = checkpoint.model_dump(mode="json")
    plan = {
        key: serialized[key]
        for key in ["source_title", "requests", "replacements", "findings", "headers"]
    }
    base_bytes = len(json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode())
    cap = 2 * 1024 * 1024
    for over in [False, True]:
        title = "a" * (cap - base_bytes + int(over))
        bounded = checkpoint.model_copy(update={"source_title": title})
        session = FixtureSession(1)
        service = PostgresTaskService(db=fixture_dependency(session))
        if over:
            with pytest.raises(CheckpointTooLargeError):
                await service.save_document_transform_checkpoint(
                    FIXTURE_TASK,
                    lease_token=FIXTURE_LEASE,
                    expected_generation=0,
                    checkpoint=bounded,
                )
            assert session.statements == []
        else:
            await service.save_document_transform_checkpoint(
                FIXTURE_TASK, lease_token=FIXTURE_LEASE, expected_generation=0, checkpoint=bounded
            )
            saved = session.statements[0].compile().params["checkpoint"]
            assert len(decrypt_cache(saved["plan_ciphertext"]).encode()) == cap
    unicode_over = checkpoint.model_copy(update={"source_title": "絵" * 700000})
    session = FixtureSession(1)
    db = session
    with pytest.raises(CheckpointTooLargeError):
        await PostgresTaskService(db=fixture_dependency(db)).save_document_transform_checkpoint(
            FIXTURE_TASK, lease_token=FIXTURE_LEASE, expected_generation=0, checkpoint=unicode_over
        )
    assert session.statements == []
