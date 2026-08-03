from __future__ import annotations

from typing import cast, override

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
from app.identifiers import DriveId, FileId
from app.sidebar.cache import CachedDriveTree, SidebarCacheService
from app.smart_create.schemas import AIMode, ProposalDestination, SmartFileCreate
from app.smart_create.service import (
    GeminiSmartProposalService,
    GeneratedProposal,
    ProposalGenerator,
)
from app.templates.schemas import TemplateCatalogRead, TemplateMimeType, TemplateRead
from app.templates.service import TemplateCatalogService


class _Drive(DriveService):
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


class _Cache(SidebarCacheService):
    @override
    async def get_drive_tree(self, db: AsyncSession, drive_id: DriveId) -> CachedDriveTree:
        raise NotImplementedError

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
                    )
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
        raise NotImplementedError


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
                    mime_type=cast("TemplateMimeType", MIME_DOC),
                    modified_time="2026-08-02T00:00:00Z",
                    category_path=["Product"],
                )
            ]
        )


class _Generator(ProposalGenerator):
    def __init__(self, proposal: GeneratedProposal | Exception) -> None:
        self.proposal = proposal

    @override
    async def generate(
        self,
        request: AIMode,
        folders: list[ProposalDestination],
        templates: list[TemplateRead],
    ) -> GeneratedProposal:
        if isinstance(self.proposal, Exception):
            raise self.proposal
        return self.proposal


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
        db=cast("AsyncSession", object()),
        drive=_Drive(),
        sidebar_cache=_Cache(),
        templates=_Templates(),
        generator=_Generator(proposal),
    )
