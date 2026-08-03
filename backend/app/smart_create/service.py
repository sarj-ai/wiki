from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Iterable
import json
import re
from typing import Annotated, override

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from loguru import logger
from pydantic import BaseModel, StringConstraints, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.drive.schemas import MIME_DOC, MIME_SLIDE, DriveNode, DriveSummary, DriveTree
from app.drive.service import DriveService
from app.identifiers import FileId
from app.sidebar.cache import SidebarCacheService
from app.smart_create.schemas import (
    AIMode,
    BlankSource,
    ProposalDestination,
    SmartFileType,
    SmartProposalRead,
    TemplateSource,
)
from app.templates.schemas import TemplateRead
from app.templates.service import TemplateCatalogService


_MAX_FOLDER_CANDIDATES = 80
_MAX_TEMPLATE_CANDIDATES = 80
_MODEL_TIMEOUT_SECONDS = 4.0
_WORD_PATTERN = re.compile(r"[a-z]+")
_MIN_ALIAS_WORD_LENGTH = 2
_MAX_ALIAS_WORD_COUNT = 3


class GeneratedProposal(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    emoji: Annotated[str, StringConstraints(min_length=1, max_length=8)]
    alias: Annotated[
        str,
        StringConstraints(
            min_length=5,
            max_length=32,
            pattern=r"^[a-z]{2,12}(?:-[a-z]{2,12}){1,2}$",
        ),
    ]
    destination_folder_id: FileId
    template_file_id: FileId | None = None


class ProposalGenerator(ABC):
    @abstractmethod
    async def generate(
        self,
        request: AIMode,
        folders: list[ProposalDestination],
        templates: list[TemplateRead],
    ) -> GeneratedProposal: ...


class VertexProposalGenerator(ProposalGenerator):
    def __init__(self) -> None:
        self._client = (
            genai.Client(
                vertexai=True,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location=settings.GEMINI_LOCATION,
            )
            if settings.GOOGLE_CLOUD_PROJECT is not None
            else None
        )

    @override
    async def generate(
        self,
        request: AIMode,
        folders: list[ProposalDestination],
        templates: list[TemplateRead],
    ) -> GeneratedProposal:
        if self._client is None:
            msg = "GOOGLE_CLOUD_PROJECT is not configured"
            raise RuntimeError(msg)
        prompt = _build_prompt(request, folders, templates)
        async with asyncio.timeout(_MODEL_TIMEOUT_SECONDS):
            response = await self._client.aio.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GeneratedProposal,
                    thinking_config=genai_types.ThinkingConfig(
                        thinking_level=genai_types.ThinkingLevel.MINIMAL
                    ),
                    http_options=genai_types.HttpOptions(
                        timeout=int(_MODEL_TIMEOUT_SECONDS * 1000)
                    ),
                ),
            )
        if isinstance(response.parsed, GeneratedProposal):
            return response.parsed
        if not response.text:
            msg = "Gemini returned an empty Smart Create proposal"
            raise ValueError(msg)
        return GeneratedProposal.model_validate_json(response.text)


class SmartProposalService(ABC):
    @abstractmethod
    async def propose(self, request: AIMode) -> SmartProposalRead: ...


class GeminiSmartProposalService(SmartProposalService):
    def __init__(
        self,
        *,
        db: AsyncSession,
        drive: DriveService,
        sidebar_cache: SidebarCacheService,
        templates: TemplateCatalogService,
        generator: ProposalGenerator,
    ) -> None:
        self._db = db
        self._drive = drive
        self._sidebar_cache = sidebar_cache
        self._templates = templates
        self._generator = generator

    @override
    async def propose(self, request: AIMode) -> SmartProposalRead:
        drives = await self._drive.list_drives()
        template_task = asyncio.create_task(self._templates.list_templates())
        trees = await self._load_cached_trees(drives)
        catalog = await template_task
        folders = _shortlist_folders(request, drives, trees)
        compatible_templates = _compatible_templates(request.file_type, catalog.templates)[
            :_MAX_TEMPLATE_CANDIDATES
        ]
        fallback = _fallback_proposal(request, folders)
        try:
            generated = await self._generator.generate(request, folders, compatible_templates)
            return _validated_proposal(
                request,
                generated,
                folders,
                compatible_templates,
            )
        except TimeoutError, RuntimeError, ValueError, ValidationError, genai_errors.APIError:
            logger.warning(
                "Smart Create proposal fell back to deterministic values file_type={file_type}",
                file_type=request.file_type,
            )
            return fallback

    async def _load_cached_trees(
        self,
        drives: list[DriveSummary],
    ) -> list[DriveTree]:
        trees: list[DriveTree] = []
        for drive in drives:
            cached = await self._sidebar_cache.peek_drive_tree(self._db, drive.id)
            if cached is not None:
                trees.append(cached.tree)
        return trees


def _build_prompt(
    request: AIMode,
    folders: list[ProposalDestination],
    templates: list[TemplateRead],
) -> str:
    context = {
        "description": request.description,
        "file_type": request.file_type,
        "folders": [folder.model_dump(mode="json") for folder in folders],
        "templates": [template.model_dump(mode="json") for template in templates],
    }
    serialized_context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    instructions = (
        "Recommend concise metadata and placement for a Sarj Wiki file. "
        "Choose destination_folder_id only from folders. Choose template_file_id only from "
        "templates, or null for a blank file. The alias must be two or three lowercase words "
        "of 2-12 ASCII letters separated by hyphens. Return one fitting emoji and no prose."
    )
    return "\n".join(  # ruff: ignore[static-join-to-f-string]
        (instructions, serialized_context)
    )


def _shortlist_folders(
    request: AIMode,
    drives: list[DriveSummary],
    trees: list[DriveTree],
) -> list[ProposalDestination]:
    drive_by_id = {drive.id: drive for drive in drives}
    destinations: list[ProposalDestination] = [
        ProposalDestination(
            folder_id=FileId(drive.id),
            drive_id=drive.id,
            drive_name=drive.name,
            folder_path=[drive.name],
        )
        for drive in drives
    ]
    for tree in trees:
        drive = drive_by_id.get(tree.drive_id)
        if drive is None:
            continue
        destinations.extend(_tree_destinations(drive, tree))
    terms = frozenset(_WORD_PATTERN.findall(request.description.casefold()))
    destinations.sort(
        key=lambda destination: (
            -_folder_score(destination, request, terms),
            tuple(part.casefold() for part in destination.folder_path),
            destination.folder_id,
        )
    )
    return destinations[:_MAX_FOLDER_CANDIDATES]


def _tree_destinations(drive: DriveSummary, tree: DriveTree) -> list[ProposalDestination]:
    by_id = {folder.id: folder for folder in tree.folders}
    return [
        ProposalDestination(
            folder_id=folder.id,
            drive_id=drive.id,
            drive_name=drive.name,
            folder_path=[drive.name, *_folder_ancestry(folder, by_id)],
        )
        for folder in tree.folders
    ]


def _folder_ancestry(folder: DriveNode, by_id: dict[FileId, DriveNode]) -> list[str]:
    names = [folder.name]
    parent_id = folder.parent_id
    visited = {folder.id}
    while parent_id is not None and parent_id in by_id and parent_id not in visited:
        visited.add(parent_id)
        parent = by_id[parent_id]
        names.append(parent.name)
        parent_id = parent.parent_id
    names.reverse()
    return names


def _folder_score(
    destination: ProposalDestination,
    request: AIMode,
    terms: frozenset[str],
) -> int:
    path_terms = set(_WORD_PATTERN.findall(" ".join(destination.folder_path).casefold()))
    score = len(terms & path_terms) * 10
    if destination.folder_id == request.invoking_folder_id:
        score += 8
    elif destination.drive_id == request.invoking_drive_id:
        score += 2
    return score


def _compatible_templates(
    file_type: SmartFileType,
    templates: Iterable[TemplateRead],
) -> list[TemplateRead]:
    mime_type = MIME_DOC if file_type == "document" else MIME_SLIDE
    return [template for template in templates if template.mime_type == mime_type]


def _validated_proposal(
    request: AIMode,
    generated: GeneratedProposal,
    folders: list[ProposalDestination],
    templates: list[TemplateRead],
) -> SmartProposalRead:
    folder = next(
        (item for item in folders if item.folder_id == generated.destination_folder_id),
        None,
    )
    if folder is None:
        msg = "Gemini selected an unknown destination"
        raise ValueError(msg)
    template = next(
        (item for item in templates if item.file_id == generated.template_file_id),
        None,
    )
    if generated.template_file_id is not None and template is None:
        msg = "Gemini selected an unknown template"
        raise ValueError(msg)
    if template is not None and template.drive_id != folder.drive_id:
        msg = "Gemini selected a template from another workspace"
        raise ValueError(msg)
    source = (
        TemplateSource(
            kind="template",
            template_file_id=template.file_id,
            template_drive_id=template.drive_id,
            template_modified_time=template.modified_time,
            mime_type=template.mime_type,
        )
        if template is not None
        else BlankSource(kind="blank", file_type=request.file_type)
    )
    return SmartProposalRead(
        name=generated.name.strip(),
        emoji=generated.emoji,
        alias=generated.alias,
        destination=folder,
        source=source,
    )


def _fallback_proposal(
    request: AIMode,
    folders: list[ProposalDestination],
) -> SmartProposalRead:
    if not folders:
        msg = "No Drive workspaces are available"
        raise ValueError(msg)
    destination = next(
        (folder for folder in folders if folder.folder_id == request.invoking_folder_id),
        folders[0],
    )
    words = _alias_words(request.description)
    title_words = request.description.split()[:8]
    title = " ".join(title_words).strip().rstrip(".,:;!?")
    if not title:
        title = "New Document" if request.file_type == "document" else "New Presentation"
    return SmartProposalRead(
        name=title[:255],
        emoji="📝" if request.file_type == "document" else "📊",
        alias="-".join(words),
        destination=destination,
        source=BlankSource(kind="blank", file_type=request.file_type),
    )


def _alias_words(description: str) -> tuple[str, str] | tuple[str, str, str]:
    words = [
        word[:12]
        for word in _WORD_PATTERN.findall(description.casefold())
        if len(word) >= _MIN_ALIAS_WORD_LENGTH
    ]
    unique_words = list(dict.fromkeys(words))
    if len(unique_words) >= _MAX_ALIAS_WORD_COUNT:
        return unique_words[0], unique_words[1], unique_words[2]
    if len(unique_words) >= _MIN_ALIAS_WORD_LENGTH:
        return unique_words[0], unique_words[1]
    if unique_words:
        return unique_words[0], "doc"
    return "new", "doc"
