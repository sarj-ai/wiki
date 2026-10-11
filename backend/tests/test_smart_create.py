from __future__ import annotations

import asyncio
from typing import override

from pydantic import ValidationError
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.drive.schemas import (
    MIME_DOC,
    MIME_FOLDER,
    DriveNode,
    DriveSummary,
    DriveTree,
    SearchResult,
    SnippetResult,
)
from app.drive.service import DriveService
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId
from app.sidebar.cache import CachedDriveTree, SidebarCacheService
from app.smart_create.errors import ReorganizationUnavailableError
from app.smart_create.schemas import AIMode, ProposalDestination, SmartFileCreate
from app.smart_create.service import (
    GeminiReorganizationService,
    GeminiSmartProposalService,
    GeneratedProposal,
    GeneratedReorganization,
    ProposalGenerator,
    ReorganizationEvidence,
    ReorganizationGenerator,
    smart_create_candidates,
)
from app.templates.schemas import TemplateCatalogRead, TemplateMimeType, TemplateRead
from app.templates.service import TemplateCatalogService
from tests.support import dependency


_DOC_MIME: TemplateMimeType = "application/vnd.google-apps.document"


class _Drive(DriveService):
    def __init__(
        self, document_text: str | None = "Product roadmap priorities and milestones"
    ) -> None:
        self.document_text = document_text
        self.document_fetches = 0

    @override
    async def list_drives(self) -> list[DriveSummary]:
        return [
            DriveSummary(id=DriveId("product"), name="Product"),
            DriveSummary(id=DriveId("people"), name="People"),
        ]

    @override
    async def walk_drive_folders(self, drive_id: DriveId) -> DriveTree:
        raise NotImplementedError

    @override
    async def list_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        raise NotImplementedError

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

    @override
    async def fetch_document_text(self, file_id: FileId) -> str | None:
        self.document_fetches += 1
        return self.document_text


class _Cache(SidebarCacheService):
    @override
    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        if (cached := await self.peek_drive_tree(db, drive_id)) is None:
            raise NotImplementedError
        return cached

    @override
    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree | None:
        if drive_id != DriveId("product"):
            return None
        return CachedDriveTree(
            tree=DriveTree(
                drive_id=drive_id,
                folders=[
                    DriveNode(
                        id=FileId("roadmaps"),
                        name="Roadmaps",
                        mime_type=MIME_FOLDER,
                        parent_id=FileId("product"),
                        modified_time=None,
                    ),
                    DriveNode(
                        id=FileId("health-relationships"),
                        name="Health & Relationships",
                        mime_type=MIME_FOLDER,
                        parent_id=FileId("product"),
                        modified_time=None,
                    ),
                    DriveNode(
                        id=FileId("roadmap-archive"),
                        name="Roadmap archive",
                        mime_type=MIME_FOLDER,
                        parent_id=FileId("roadmaps"),
                        modified_time=None,
                    ),
                    DriveNode(
                        id=FileId("roadmap-deep"),
                        name="Deep roadmap archive",
                        mime_type=MIME_FOLDER,
                        parent_id=FileId("roadmap-archive"),
                        modified_time=None,
                    ),
                ],
                files=[],
                files_complete=True,
            ),
            refreshed_at=None,
        )

    @override
    async def get_folder_children(self, folder_id: FileId) -> list[DriveNode]:
        raise NotImplementedError

    @override
    async def invalidate_drive(self, db: AsyncSession, drive_id: DriveId) -> None:
        raise NotImplementedError

    @override
    async def refresh_drive(self, db: AsyncSession, drive_id: DriveId) -> DriveTree:
        cached = await self.get_drive_tree(db, drive_id)
        return cached.tree


class _Templates(TemplateCatalogService):
    @override
    async def list_templates(self, *, refresh: bool = False) -> TemplateCatalogRead:
        return TemplateCatalogRead(
            templates=[
                TemplateRead(
                    file_id=FileId("prd"),
                    drive_id=DriveId("product"),
                    drive_name="Product",
                    name="PRD",
                    description="Product requirements",
                    mime_type=_DOC_MIME,
                    modified_time="2026-08-02T00:00:00Z",
                    category_path=["Product"],
                ),
                TemplateRead(
                    file_id=FileId("people-template"),
                    drive_id=DriveId("people"),
                    drive_name="People",
                    name="People template",
                    description="People operations",
                    mime_type=_DOC_MIME,
                    modified_time="2026-08-02T00:00:00Z",
                    category_path=["People"],
                ),
            ]
        )


class _RecordingCache(_Cache):
    def __init__(self) -> None:
        self.loaded: list[DriveId] = []

    @override
    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree | None:
        self.loaded.append(drive_id)
        return await super().peek_drive_tree(db, drive_id)


class _PendingTemplates(_Templates):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    @override
    async def list_templates(self, *, refresh: bool = False) -> TemplateCatalogRead:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        raise AssertionError


class _BlockingCache(_Cache):
    def __init__(self, templates: _PendingTemplates, *, fail: bool) -> None:
        self.templates = templates
        self.fail = fail

    @override
    async def peek_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree | None:
        await self.templates.started.wait()
        if self.fail:
            raise ValueError
        await asyncio.Event().wait()
        raise AssertionError


class _OverlappingDrive(_Drive):
    def __init__(self, content_started: asyncio.Event, tree_started: asyncio.Event) -> None:
        super().__init__()
        self.content_started = content_started
        self.tree_started = tree_started

    @override
    async def fetch_document_text(self, file_id: FileId) -> str | None:
        self.content_started.set()
        await self.tree_started.wait()
        return await super().fetch_document_text(file_id)


class _OverlappingCache(_Cache):
    def __init__(self, content_started: asyncio.Event, tree_started: asyncio.Event) -> None:
        self.content_started = content_started
        self.tree_started = tree_started

    @override
    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        self.tree_started.set()
        await self.content_started.wait()
        return await super().get_drive_tree(db, drive_id)


class _Generator(ProposalGenerator):
    def __init__(self, proposal: GeneratedProposal | Exception) -> None:
        self.proposal = proposal
        self.folders: list[ProposalDestination] = []
        self.templates: list[TemplateRead] = []

    @override
    async def generate(
        self,
        request: AIMode,
        folders: list[ProposalDestination],
        templates: list[TemplateRead],
    ) -> GeneratedProposal:
        self.folders = folders
        self.templates = templates
        if isinstance(self.proposal, Exception):
            raise self.proposal
        return self.proposal


class _ReorganizationGenerator(ReorganizationGenerator):
    def __init__(self, proposal: GeneratedReorganization | Exception) -> None:
        self.proposal = proposal
        self.evidence: ReorganizationEvidence | None = None

    @override
    async def generate(
        self,
        *,
        current: ProposalDestination,
        evidence: ReorganizationEvidence,
        folders: list[ProposalDestination],
    ) -> GeneratedReorganization:
        del current, folders
        self.evidence = evidence
        if isinstance(self.proposal, Exception):
            raise self.proposal
        return self.proposal


class _Files:
    def __init__(
        self,
        *,
        can_edit: bool = True,
        mime_type: str = MIME_DOC,
        name: str = "Quarterly product roadmap",
    ) -> None:
        self.can_edit = can_edit
        self.mime_type = mime_type
        self.name = name

    async def get(self, file_id: FileId) -> FileResponse:
        return FileResponse(
            id=file_id,
            name=self.name,
            mime_type=self.mime_type,
            parent_folder_id=FileId("product"),
            drive_id=DriveId("product"),
            modified_time=None,
            can_edit=self.can_edit,
        )


async def test_accepts_enumerated_same_workspace_template() -> None:
    service = _service(
        GeneratedProposal(
            name="Quarterly roadmap",
            emoji="🗺️",
            alias="quarterly-roadmap",
            destination_folder_id=FileId("roadmaps"),
            template_file_id=FileId("prd"),
        )
    )

    proposal = await service.propose(_request())

    assert proposal.destination.folder_id == "roadmaps"
    assert proposal.source.kind == "template"
    assert proposal.source.template_file_id == "prd"


async def test_invalid_model_identifier_uses_deterministic_fallback() -> None:
    service = _service(
        GeneratedProposal(
            name="Invented",
            emoji="🧪",
            alias="invented-folder",
            destination_folder_id=FileId("not-visible"),
        )
    )

    proposal = await service.propose(_request())

    assert proposal.destination.folder_id == "roadmaps"
    assert proposal.source.kind == "blank"
    assert proposal.alias == "quarterly-product-roadmap"


async def test_vertex_failure_uses_deterministic_fallback() -> None:
    proposal = await _service(RuntimeError("offline")).propose(_request())

    assert proposal.name == "Quarterly product roadmap"
    assert proposal.emoji == "📝"
    assert proposal.destination.folder_id == "roadmaps"


async def test_smart_create_only_exposes_the_invoking_drive() -> None:
    generator = _Generator(
        GeneratedProposal(
            name="Quarterly roadmap",
            emoji="🗺️",
            alias="quarterly-roadmap",
            destination_folder_id=FileId("roadmaps"),
            template_file_id=FileId("prd"),
        )
    )
    cache = _RecordingCache()
    service = GeminiSmartProposalService(
        db=AsyncSession(),
        drive=_Drive(),
        sidebar_cache=cache,
        templates=_Templates(),
        generator=generator,
    )

    await service.propose(_request())

    assert {folder.drive_id for folder in generator.folders} == {"product"}
    folder_ids = {str(folder.folder_id) for folder in generator.folders}
    assert "roadmap-archive" in folder_ids
    assert "roadmap-deep" not in folder_ids
    assert {template.drive_id for template in generator.templates} == {"product"}
    assert cache.loaded == [DriveId("product")]


@pytest.mark.parametrize("fail", [False, True], ids=["request-cancelled", "cache-failure"])
async def test_smart_create_cleans_up_pending_catalog_when_primary_read_aborts(fail: bool) -> None:
    templates = _PendingTemplates()
    service = GeminiSmartProposalService(
        db=AsyncSession(),
        drive=_Drive(),
        sidebar_cache=_BlockingCache(templates, fail=fail),
        templates=templates,
        generator=_Generator(RuntimeError("The generator must not be called")),
    )
    task = asyncio.create_task(service.propose(_request()))
    await asyncio.wait_for(templates.started.wait(), timeout=1)
    if not fail:
        task.cancel()
    with pytest.raises(ValueError if fail else asyncio.CancelledError):
        await task
    assert templates.cancelled.is_set()


@pytest.mark.parametrize(
    ("candidate_depth", "expected"),
    [
        (1, {"product", "planning", "quarterly", "future", "peer-child"}),
        (2, {"product", "planning", "quarterly", "future", "peer-child", "deep"}),
        (3, {"product", "planning", "quarterly", "future", "peer-child", "deep"}),
        (None, {"product", "planning", "quarterly", "future", "peer-child", "deep"}),
    ],
)
def test_candidate_depth_is_relative_and_retains_current_ancestry(
    candidate_depth: int | None, expected: set[str]
) -> None:
    drive = DriveSummary(id=DriveId("product"), name="Product")
    tree = DriveTree(
        drive_id=drive.id,
        folders=[
            DriveNode(
                id=FileId("planning"),
                name="Planning",
                mime_type=MIME_FOLDER,
                parent_id=None,
                modified_time=None,
            ),
            DriveNode(
                id=FileId("quarterly"),
                name="Quarterly",
                mime_type=MIME_FOLDER,
                parent_id=FileId("planning"),
                modified_time=None,
            ),
            DriveNode(
                id=FileId("future"),
                name="Future",
                mime_type=MIME_FOLDER,
                parent_id=FileId("quarterly"),
                modified_time=None,
            ),
            DriveNode(
                id=FileId("peer-child"),
                name="Peer child",
                mime_type=MIME_FOLDER,
                parent_id=FileId("planning"),
                modified_time=None,
            ),
            DriveNode(
                id=FileId("deep"),
                name="Deep",
                mime_type=MIME_FOLDER,
                parent_id=FileId("future"),
                modified_time=None,
            ),
        ],
        files=[],
        files_complete=True,
    )

    candidates = smart_create_candidates(
        AIMode(
            kind="ai",
            description="Quarterly product roadmap",
            file_type="document",
            invoking_folder_id=FileId("quarterly"),
            invoking_drive_id=DriveId("product"),
        ),
        [drive],
        [tree],
        candidate_depth=candidate_depth,
    )

    assert {str(candidate.folder_id) for candidate in candidates} == expected


@pytest.mark.parametrize(
    ("name", "emoji", "alias", "description", "expected_destination"),
    [
        (
            "Lunar greenhouse maintenance checklist",
            "🌱",
            "lunar-greenhouse",
            "Plant health reviews, irrigation sensors, and soil moisture calibration",
            "roadmaps",
        ),
        (
            "Annual medical checkup plan",
            "🩺",
            "medical-checkup",
            "Annual medical checkup plan with doctor appointments",
            "health-relationships",
        ),
    ],
    ids=["rejects-ambiguous-overlap", "accepts-explicit-category-evidence"],
)
async def test_sensitive_destination_requires_explicit_category_evidence(
    name: str,
    emoji: str,
    alias: str,
    description: str,
    expected_destination: str,
) -> None:
    service = _service(
        GeneratedProposal(
            name=name,
            emoji=emoji,
            alias=alias,
            destination_folder_id=FileId("health-relationships"),
        )
    )

    proposal = await service.propose(
        AIMode(
            kind="ai",
            description=description,
            file_type="document",
            invoking_folder_id=FileId("roadmaps"),
            invoking_drive_id=DriveId("product"),
        )
    )

    assert proposal.destination.folder_id == expected_destination


async def test_reorganization_proposes_folder_only_within_current_drive() -> None:
    service = _reorganization_service(
        GeneratedReorganization(
            destination_folder_id=FileId("roadmaps"),
            reason="Roadmaps belong with the product planning documents.",
        )
    )

    proposal = await service.propose(FileId("document"))

    assert proposal.current_destination.folder_id == "product"
    assert proposal.suggested_destination is not None
    assert proposal.suggested_destination.folder_id == "roadmaps"
    assert proposal.suggested_destination.drive_id == "product"


async def test_reorganization_truncates_verbose_model_reason() -> None:
    service = _reorganization_service(
        GeneratedReorganization(
            destination_folder_id=FileId("roadmaps"),
            reason="explanation " * 40,
        )
    )

    proposal = await service.propose(FileId("document"))

    assert proposal.reason is not None
    assert len(proposal.reason) <= 200
    assert not proposal.reason.endswith(" ")


async def test_reorganization_returns_no_suggestion_when_current_folder_is_best() -> None:
    proposal = await _reorganization_service(
        GeneratedReorganization(
            destination_folder_id=FileId("product"),
            reason="The workspace root is the best fit.",
        )
    ).propose(FileId("document"))

    assert proposal.suggested_destination is None
    assert proposal.reason is None


async def test_reorganization_reports_generator_failure_instead_of_claiming_best_location() -> None:
    with pytest.raises(ReorganizationUnavailableError):
        await _reorganization_service(RuntimeError("offline")).propose(FileId("document"))


async def test_reorganization_requires_exported_document_content() -> None:
    with pytest.raises(ReorganizationUnavailableError):
        await _reorganization_service(
            GeneratedReorganization(
                destination_folder_id=FileId("product"),
                reason="Current folder is best.",
            ),
            document_text=None,
        ).propose(FileId("document"))


async def test_reorganization_rejects_read_only_files_before_exporting_content() -> None:
    drive = _Drive("This content must not be exported")
    generator = _ReorganizationGenerator(
        GeneratedReorganization(
            destination_folder_id=FileId("product"),
            reason="Current folder is best.",
        )
    )
    service = GeminiReorganizationService(
        db=AsyncSession(),
        drive=drive,
        files=dependency(_Files(can_edit=False)),
        sidebar_cache=_Cache(),
        generator=generator,
    )

    with pytest.raises(FileNotInScopeError, match="not editable"):
        await service.propose(FileId("document"))

    assert drive.document_fetches == 0
    assert generator.evidence is None


async def test_reorganization_passes_content_and_metadata_to_generator() -> None:
    generator = _ReorganizationGenerator(
        GeneratedReorganization(
            destination_folder_id=FileId("product"),
            reason="Current folder is best.",
        )
    )
    service = GeminiReorganizationService(
        db=AsyncSession(),
        drive=_Drive("Customer interview findings and roadmap tradeoffs"),
        files=dependency(_Files()),
        sidebar_cache=_Cache(),
        generator=generator,
    )

    await service.propose(FileId("document"))

    assert generator.evidence is not None
    assert generator.evidence.content == "Customer interview findings and roadmap tradeoffs"
    assert generator.evidence.file_name == "Quarterly product roadmap"


async def test_reorganization_overlaps_export_with_independent_tree_read() -> None:
    content_started = asyncio.Event()
    tree_started = asyncio.Event()
    generator = _ReorganizationGenerator(
        GeneratedReorganization(
            destination_folder_id=FileId("roadmaps"),
            reason="Roadmaps belong with the product planning documents.",
        )
    )
    drive = _OverlappingDrive(content_started, tree_started)
    service = GeminiReorganizationService(
        db=AsyncSession(),
        drive=drive,
        files=dependency(_Files()),
        sidebar_cache=_OverlappingCache(content_started, tree_started),
        generator=generator,
    )
    proposal = await asyncio.wait_for(service.propose(FileId("document")), timeout=1)

    assert drive.document_fetches == 1
    assert generator.evidence is not None
    assert generator.evidence.content == "Product roadmap priorities and milestones"
    assert proposal.suggested_destination is not None
    assert proposal.suggested_destination.folder_id == "roadmaps"


async def test_reorganization_uses_name_and_metadata_for_non_google_docs() -> None:
    generator = _ReorganizationGenerator(
        GeneratedReorganization(
            destination_folder_id=FileId("product"),
            reason="Current folder is best.",
        )
    )
    drive = _Drive("This content must not be fetched")
    service = GeminiReorganizationService(
        db=AsyncSession(),
        drive=drive,
        files=dependency(_Files(mime_type="application/pdf", name="Quarterly investor report.pdf")),
        sidebar_cache=_Cache(),
        generator=generator,
    )

    await service.propose(FileId("report"))

    assert generator.evidence is not None
    assert not generator.evidence.content
    assert generator.evidence.file_name == "Quarterly investor report.pdf"
    assert drive.document_fetches == 0


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "ai",
            "description": "Quarterly roadmap",
            "file_type": "document",
            "template_file_id": "prd",
        },
        {
            "source": {
                "kind": "blank",
                "file_type": "document",
                "template_file_id": "prd",
            },
            "parent_folder_id": "roadmaps",
            "name": "Roadmap",
            "emoji": "📝",
            "alias": "quarterly-roadmap",
        },
    ],
    ids=["ai-with-template-field", "blank-with-template-field"],
)
def test_public_mode_schemas_reject_crossed_variant_fields(
    payload: dict[str, object],
) -> None:
    schema = AIMode if payload.get("kind") == "ai" else SmartFileCreate
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def _request() -> AIMode:
    return AIMode(
        kind="ai",
        description="Quarterly product roadmap",
        file_type="document",
        invoking_folder_id=FileId("roadmaps"),
        invoking_drive_id=DriveId("product"),
    )


def _service(proposal: GeneratedProposal | Exception) -> GeminiSmartProposalService:
    return GeminiSmartProposalService(
        db=AsyncSession(),
        drive=_Drive(),
        sidebar_cache=_Cache(),
        templates=_Templates(),
        generator=_Generator(proposal),
    )


def _reorganization_service(
    proposal: GeneratedReorganization | Exception,
    *,
    document_text: str | None = "Product roadmap priorities and milestones",
) -> GeminiReorganizationService:
    return GeminiReorganizationService(
        db=AsyncSession(),
        drive=_Drive(document_text),
        files=dependency(_Files()),
        sidebar_cache=_Cache(),
        generator=_ReorganizationGenerator(proposal),
    )
