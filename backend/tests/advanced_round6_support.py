from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from typing import Any, override
import uuid

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.header_provenance import (
    HeaderIdentityEvidence,
    HeaderIdentityFinalization,
    ManagedHeaderIdentity,
)
from app.document_transform.schemas import (
    CopyCheckpoint,
    CopyCheckpointStage,
    DocumentOutput,
    DocumentTransformResult,
    InPlaceCheckpoint,
    InPlaceCheckpointStage,
    TransformFinding,
    TransformMode,
    ValidationSummary,
    WordCopyCheckpoint,
)
from app.document_transform.versions import DOCUMENT_PIPELINE_VERSION
from app.drive.schemas import (
    MIME_FOLDER,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
    SnippetResult,
)
from app.drive.service import DriveService
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, DriveId, FileId, TaskId
from app.models.task import Task
from app.models.wiki import DocumentHeaderProvenance
from app.sidebar.cache import CachedDriveTree, SidebarCacheService
from app.smart_create.schemas import AIMode, SmartFileCreate
from app.smart_create.service import GeneratedProposal, ProposalGenerator
from app.tasks.schemas import TaskKind, TaskStatus
from app.templates.schemas import TemplateCatalogRead
from app.templates.service import TemplateCatalogService


FIXTURE_TIME = datetime(2026, 10, 11, tzinfo=UTC)
FIXTURE_OWNER = uuid.UUID(int=601)
FIXTURE_TASK = TaskId(uuid.UUID(int=602))
FIXTURE_LEASE = uuid.UUID(int=603)
FIXTURE_DRIVE = DriveSummary(id=DriveId("advanced-drive"), name="Workspace 絵")


def folder_node(file_id: FileId, name: str, parent_id: FileId | None) -> DriveNode:
    return DriveNode(
        id=FileId(file_id),
        name=name,
        mime_type=MIME_FOLDER,
        parent_id=FileId(parent_id) if parent_id is not None else None,
        modified_time="2026-10-11T00:00:00Z",
    )


class FixtureDrive(DriveService):
    def __init__(
        self,
        *,
        drives: list[DriveSummary] | None = None,
        children: dict[FileId, list[DriveNode]] | None = None,
        failed_folders: set[FileId] | None = None,
    ) -> None:
        self.drives = drives if drives is not None else [FIXTURE_DRIVE]
        self.children = children or {}
        self.failed_folders = failed_folders or set()
        self.calls: defaultdict[FileId, int] = defaultdict(int)
        self.inventory_calls = 0

    @override
    async def list_drives(self) -> list[DriveSummary]:
        self.inventory_calls += 1
        return self.drives

    @override
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
        raise NotImplementedError

    @override
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        self.calls[folder_id] += 1
        if folder_id in self.failed_folders:
            message = "Fixture provider denied this template category"
            raise PermissionError(message)
        return self.children.get(folder_id, [])

    @override
    async def search_drive(
        self, drive_id: DriveId, query: str, *, limit: int = 50
    ) -> list[SearchResult]:
        raise NotImplementedError

    @override
    async def search(self, query: str, *, limit_per_drive: int = 20) -> list[SearchResult]:
        raise NotImplementedError

    @override
    async def fetch_snippet(
        self, file_id: FileId, query: str, *, mime_type: str | None = None
    ) -> SnippetResult:
        raise NotImplementedError


class FixtureSidebarCache(SidebarCacheService):
    def __init__(self, tree: DriveTree | None = None) -> None:
        self.tree = tree
        self.reads = 0

    @override
    async def get_drive_tree(self, db: Any, drive_id: DriveId) -> CachedDriveTree:
        self.reads += 1
        if self.tree is None:
            raise LookupError
        return CachedDriveTree(tree=self.tree, refreshed_at=FIXTURE_TIME)

    @override
    async def peek_drive_tree(self, db: Any, drive_id: DriveId) -> CachedDriveTree | None:
        self.reads += 1
        return (
            CachedDriveTree(tree=self.tree, refreshed_at=FIXTURE_TIME)
            if self.tree is not None
            else None
        )

    @override
    async def get_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        raise NotImplementedError

    @override
    async def invalidate_drive(self, db: Any, drive_id: DriveId) -> None:
        raise NotImplementedError

    @override
    async def refresh_drive(self, db: Any, drive_id: DriveId) -> DriveTree:
        if self.tree is None:
            raise LookupError
        return self.tree


class FixtureTemplates(TemplateCatalogService):
    def __init__(self, catalog: TemplateCatalogRead | None = None) -> None:
        self.catalog = catalog or TemplateCatalogRead(templates=[])
        self.reads = 0

    @override
    async def list_templates(self, *, refresh: bool = False) -> TemplateCatalogRead:
        self.reads += 1
        return self.catalog


class FixtureProposalGenerator(ProposalGenerator):
    def __init__(self, proposal: GeneratedProposal | Exception) -> None:
        self.proposal = proposal
        self.calls = 0

    @override
    async def generate(self, request: AIMode, folders: Any, templates: Any) -> GeneratedProposal:
        self.calls += 1
        if isinstance(self.proposal, Exception):
            raise self.proposal
        return self.proposal


class FixtureGenaiModels:
    def __init__(self, text: str) -> None:
        self.response = SimpleNamespace(parsed=None, text=text)
        self.prompt = ""
        self.config: object | None = None
        self.calls = 0

    async def generate_content(self, *, model: str, contents: str, config: object) -> Any:
        self.prompt = contents
        self.config = config
        self.calls += 1
        return self.response


class FixtureGenaiClient:
    def __init__(self, text: str) -> None:
        self.models = FixtureGenaiModels(text)
        self.aio = SimpleNamespace(models=self.models)


class FixtureResult:
    def __init__(self, row: object) -> None:
        self.row = row

    def scalar_one_or_none(self) -> Any:
        return self.row

    def scalar_one(self) -> Any:
        return self.row


class FixtureSession:
    def __init__(self, *rows: object) -> None:
        self.rows = iter(rows)
        self.statements: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement: object) -> FixtureResult:
        self.statements.append(statement)
        return FixtureResult(next(self.rows))

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def ai_request(description: str = "Project decision record") -> AIMode:
    return AIMode(
        kind="ai",
        description=description,
        file_type="document",
        invoking_drive_id=FIXTURE_DRIVE.id,
        invoking_folder_id=None,
    )


def generated_proposal(folder_id: FileId | None = None) -> GeneratedProposal:
    return GeneratedProposal(
        name="Project Decision",
        emoji="📝",
        alias="project-decision",
        destination_folder_id=folder_id or FileId(FIXTURE_DRIVE.id),
    )


def task_row(kind: TaskKind = TaskKind.FILE_SMART_CREATE) -> Task:
    return Task(
        id=FIXTURE_TASK,
        owner_user_id=FIXTURE_OWNER,
        kind=kind,
        status=TaskStatus.PENDING,
        idempotency_key=uuid.UUID(int=604),
        request_hash="a" * 64,
        payload={},
        result=None,
        checkpoint=None,
        checkpoint_generation=0,
        resource_id=None,
        error_code=None,
        error_detail=None,
        attempt_count=0,
        created_at=FIXTURE_TIME,
        started_at=None,
        finished_at=None,
        updated_at=FIXTURE_TIME,
        expires_at=FIXTURE_TIME + timedelta(days=7),
    )


def smart_payload() -> SmartFileCreate:
    return SmartFileCreate.model_validate(
        {
            "source": {"kind": "blank", "file_type": "document"},
            "parent_folder_id": "folder-1",
            "name": "Project decision",
            "emoji": "📝",
            "alias": "project-decision",
        }
    )


def in_place_checkpoint(*, request_count: int = 1, title: str = "Decision 絵") -> InPlaceCheckpoint:
    return InPlaceCheckpoint(
        document_id=FileId("document-1"),
        source_title=title,
        source_sha256="a" * 64,
        source_modified_time="2026-10-11T00:00:00Z",
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        pipeline_version=DOCUMENT_PIPELINE_VERSION,
        request_builder_version="fixture-1",
        unslop_catalog_sha256="b" * 64,
        original_semantic_sha256="c" * 64,
        expected_semantic_sha256="c" * 64,
        structural_sha256="d" * 64,
        protected_structure_sha256="d" * 64,
        body_sha256="e" * 64,
        header_safe_sha256="f" * 64,
        source_revision_id=DocsRevisionId("revision-1"),
        current_revision_id=DocsRevisionId("revision-1"),
        stage=InPlaceCheckpointStage.HEADERS,
        requests=[
            {
                "updateTextStyle": {
                    "textStyle": {"weightedFontFamily": {"fontFamily": "Fixture 絵"}},
                    "range": {"tabId": "tab-1", "startIndex": index + 1, "endIndex": index + 2},
                    "fields": "weightedFontFamily",
                }
            }
            for index in range(request_count)
        ],
        replacements={f"z-{index}": f"Replacement 絵 {index}" for index in range(request_count)},
        findings=[TransformFinding(code="fixture", detail="No provider calls")],
        applied_patch_count=0,
        deterministic_patch_count=0,
        model_patch_count=0,
        llm_invoked=False,
        model_ids=[],
        headers={"tab-1": "header-1"},
        changed=True,
    )


def word_checkpoint() -> WordCopyCheckpoint:
    return WordCopyCheckpoint(
        request_sha256="a" * 64,
        source_binding={
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "id": "source-word",
        },
        source_sha256="b" * 64,
        output_sha256="c" * 64,
        transformer_version="fixture-1",
        target_font="Fixture 絵",
        output_name="Decision",
        reserved_output_id=FileId("output-word"),
        changed_run_count=0,
        stage=CopyCheckpointStage.PLANNED,
    )


def verified_copy_checkpoint(*, findings_count: int = 1) -> CopyCheckpoint:
    evidence = HeaderIdentityEvidence(
        operation_id=FIXTURE_TASK,
        file_id=FileId("output-1"),
        profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        title_sha256="a" * 64,
        tab_ids=(DocsTabId("tab-1"),),
        headers=(
            ManagedHeaderIdentity(
                tab_id=DocsTabId("tab-1"),
                header_id=DocsSegmentId("header-1"),
                fingerprint_sha256="b" * 64,
            ),
        ),
    )
    output = DocumentOutput(
        file_id=FileId("output-1"), name="Decision", parent_folder_id=FileId("folder-1")
    )
    result = DocumentTransformResult(
        operation_id=FIXTURE_TASK,
        header_identity=evidence,
        source_sha256="c" * 64,
        output=output,
        mode=TransformMode.FORMAT,
        applied_patch_count=0,
        findings=[
            TransformFinding(code=f"fixture-{index}", detail=f"Detail 絵 {index}")
            for index in range(findings_count)
        ],
        validation=ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=True,
            structure_preserved=True,
            unsafe_patch_count=0,
        ),
        model_ids=[],
        tool_versions={"fixture": "1"},
        pipeline_version=DOCUMENT_PIPELINE_VERSION,
        needs_review=False,
        output_revision_id=DocsRevisionId("output-revision-1"),
    )
    digest = hashlib.sha256(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    revision_digest = hashlib.sha256(b"output-revision-1").hexdigest()
    return CopyCheckpoint(
        source_title="Decision",
        source_sha256="c" * 64,
        source_modified_time=None,
        source_revision_id=None,
        source_parent_folder_id=FileId("folder-1"),
        brand_profile_id=CURRENT_BRAND_MANIFEST.profile_id,
        brand_profile_sha256=CURRENT_BRAND_MANIFEST.sha256,
        pipeline_version=DOCUMENT_PIPELINE_VERSION,
        request_builder_version="fixture-1",
        unslop_catalog_sha256="d" * 64,
        original_semantic_sha256="e" * 64,
        expected_semantic_sha256="e" * 64,
        plan_sha256="f" * 64,
        manifest_sha256="a" * 64,
        logo_sha256=None,
        output_name="Decision",
        replacements={},
        findings=result.findings,
        applied_patch_count=0,
        deterministic_patch_count=0,
        model_patch_count=0,
        llm_invoked=False,
        model_ids=[],
        stage=CopyCheckpointStage.VERIFIED,
        output=output,
        output_revision_id=DocsRevisionId("output-revision-1"),
        result=result,
        managed_headers={"tab-1": "header-1"},
        header_finalization=HeaderIdentityFinalization(
            result_sha256=digest, verified_revision_sha256=revision_digest
        ),
    )


def provenance_row(checkpoint: CopyCheckpoint) -> DocumentHeaderProvenance:
    assert checkpoint.result is not None
    assert checkpoint.result.header_identity is not None
    assert checkpoint.header_finalization is not None
    return DocumentHeaderProvenance(
        owner_user_id=FIXTURE_OWNER,
        file_id=checkpoint.result.output.file_id,
        producer_operation_id=FIXTURE_TASK,
        evidence=checkpoint.result.header_identity.model_dump(mode="json"),
        result_sha256=checkpoint.header_finalization.result_sha256,
        verified_revision_sha256=checkpoint.header_finalization.verified_revision_sha256,
    )


def fixture_dependency(value: FixtureSession | FixtureGenaiClient) -> Any:
    return value
