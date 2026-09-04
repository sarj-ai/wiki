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
from app.files.errors import FileNotInScopeError
from app.files.service import FilesService
from app.identifiers import FileId
from app.sidebar.cache import SidebarCacheService
from app.smart_create.errors import ReorganizationUnavailableError
from app.smart_create.schemas import (
    AIMode,
    BlankSource,
    ProposalDestination,
    ReorganizationProposalRead,
    SmartFileType,
    SmartProposalRead,
    TemplateSource,
)
from app.templates.schemas import TemplateRead
from app.templates.service import TemplateCatalogService


_MAX_FOLDER_CANDIDATES = 80
_MAX_TEMPLATE_CANDIDATES = 80
_MODEL_TIMEOUT_SECONDS = 8.0
_REORGANIZATION_MODEL_TIMEOUT_SECONDS = 30.0
_WORD_PATTERN = re.compile(r"[a-z]+")
_MIN_ALIAS_WORD_LENGTH = 2
_MAX_ALIAS_WORD_COUNT = 3
_MIN_REORGANIZATION_TERM_LENGTH = 4
_MAX_GENERATED_REORGANIZATION_REASON_CHARS = 1_000
_MAX_REORGANIZATION_REASON_CHARS = 200
_SENSITIVE_DESTINATION_INTENT_TERMS = {
    "banking": frozenset({"account", "bank", "banking", "deposit", "loan", "statement"}),
    "finance": frozenset(
        {"budget", "expense", "finance", "financial", "invoice", "investment", "payroll", "tax"}
    ),
    "health": frozenset(
        {
            "appointment",
            "checkup",
            "clinic",
            "diagnosis",
            "doctor",
            "healthcare",
            "hospital",
            "medical",
            "prescription",
            "therapy",
            "wellness",
        }
    ),
    "identity": frozenset(
        {"birth", "certificate", "identity", "license", "passport", "social", "ssn"}
    ),
    "immigration": frozenset({"citizenship", "immigration", "passport", "residency", "visa"}),
    "legal": frozenset({"attorney", "compliance", "contract", "court", "law", "legal", "lawsuit"}),
    "relationships": frozenset(
        {"family", "marriage", "partner", "relationship", "spouse", "wedding"}
    ),
}
_REORGANIZATION_STOP_WORDS = frozenset(
    {
        "about",
        "also",
        "been",
        "from",
        "have",
        "into",
        "that",
        "their",
        "this",
        "with",
        "would",
    }
)


class ReorganizationEvidence(BaseModel):
    content: str
    description: str | None
    file_name: str
    modified_time: str | None


class GeneratedReorganization(BaseModel):
    destination_folder_id: FileId
    reason: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=_MAX_GENERATED_REORGANIZATION_REASON_CHARS,
        ),
    ]


class ReorganizationGenerator(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        current: ProposalDestination,
        evidence: ReorganizationEvidence,
        folders: list[ProposalDestination],
    ) -> GeneratedReorganization: ...


class VertexReorganizationGenerator(ReorganizationGenerator):
    def __init__(self, *, project: str | None = None, model: str | None = None) -> None:
        resolved_project = project or settings.GOOGLE_CLOUD_PROJECT
        self._model = model or settings.GEMINI_REORGANIZATION_MODEL
        self._client = (
            genai.Client(
                vertexai=True,
                project=resolved_project,
                location=settings.GEMINI_LOCATION,
            )
            if resolved_project is not None
            else None
        )

    @override
    async def generate(
        self,
        *,
        current: ProposalDestination,
        evidence: ReorganizationEvidence,
        folders: list[ProposalDestination],
    ) -> GeneratedReorganization:
        if self._client is None:
            msg = "GOOGLE_CLOUD_PROJECT is not configured"
            raise RuntimeError(msg)
        prompt = _build_reorganization_prompt(evidence, current, folders)
        async with asyncio.timeout(_REORGANIZATION_MODEL_TIMEOUT_SECONDS):
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GeneratedReorganization,
                    thinking_config=genai_types.ThinkingConfig(
                        thinking_level=genai_types.ThinkingLevel.LOW
                    ),
                    http_options=genai_types.HttpOptions(
                        timeout=int(_REORGANIZATION_MODEL_TIMEOUT_SECONDS * 1000)
                    ),
                ),
            )
        if isinstance(response.parsed, GeneratedReorganization):
            return response.parsed
        if not response.text:
            msg = "Gemini returned an empty Re-organize proposal"
            raise ValueError(msg)
        return GeneratedReorganization.model_validate_json(response.text)


class ReorganizationService(ABC):
    @abstractmethod
    async def propose(self, file_id: FileId) -> ReorganizationProposalRead: ...


class GeminiReorganizationService(ReorganizationService):
    def __init__(
        self,
        *,
        db: AsyncSession,
        drive: DriveService,
        files: FilesService,
        sidebar_cache: SidebarCacheService,
        generator: ReorganizationGenerator,
    ) -> None:
        self._db = db
        self._drive = drive
        self._files = files
        self._sidebar_cache = sidebar_cache
        self._generator = generator

    @override
    async def propose(self, file_id: FileId) -> ReorganizationProposalRead:
        file, drives = await asyncio.gather(self._files.get(file_id), self._drive.list_drives())
        if not file.can_edit:
            msg = "File is not editable"
            raise FileNotInScopeError(msg)
        drive = next((item for item in drives if item.id == file.drive_id), None)
        if drive is None:
            msg = "File not available in a Drive workspace"
            raise FileNotInScopeError(msg)
        content = ""
        if file.mime_type == MIME_DOC:
            exported_content = await self._drive.fetch_document_text(file_id)
            if exported_content is None:
                logger.warning("Re-organize content unavailable")
                raise ReorganizationUnavailableError
            content = exported_content
        evidence = ReorganizationEvidence(
            content=content,
            description=file.description,
            file_name=file.name,
            modified_time=file.modified_time,
        )
        cached = await self._sidebar_cache.get_drive_tree(self._db, drive.id)
        folders = organization_candidates(evidence, file.parent_folder_id, drive, cached.tree)
        current_id = file.parent_folder_id or FileId(drive.id)
        current = next((item for item in folders if item.folder_id == current_id), None)
        if current is None:
            tree = await self._sidebar_cache.refresh_drive(self._db, drive.id)
            folders = organization_candidates(evidence, file.parent_folder_id, drive, tree)
            current = next((item for item in folders if item.folder_id == current_id), None)
        if current is None:
            msg = "The file's current folder is not available in this workspace"
            raise FileNotInScopeError(msg)
        try:
            generated = await self._generator.generate(
                current=current,
                evidence=evidence,
                folders=folders,
            )
            return _validated_reorganization(generated, folders, current)
        except (
            TimeoutError,
            RuntimeError,
            ValueError,
            ValidationError,
            genai_errors.APIError,
        ) as exc:
            logger.warning(
                "Re-organize proposal failed error_type={error_type}",
                error_type=type(exc).__name__,
            )
            raise ReorganizationUnavailableError from exc


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
        folders = smart_create_candidates(request, drives, trees, candidate_depth=1)
        compatible_templates = [
            template
            for template in _compatible_templates(request.file_type, catalog.templates)
            if template.drive_id == request.invoking_drive_id
        ][:_MAX_TEMPLATE_CANDIDATES]
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


def _build_reorganization_prompt(
    evidence: ReorganizationEvidence,
    current: ProposalDestination,
    folders: list[ProposalDestination],
) -> str:
    context = {
        "current_destination": current.model_dump(mode="json"),
        "document": evidence.model_dump(mode="json"),
        "folders": [folder.model_dump(mode="json") for folder in folders],
    }
    serialized_context = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    instructions = (
        "Choose the best folder for this existing Sarj Wiki file using its available content, title, "
        "description, modified time, and the meaning of each complete folder path. Empty content "
        "means this is not a Google Doc, so rely on its title and metadata. Do not infer a "
        "sensitive category from a person's name or a weak keyword match. Keep the current folder "
        "when the evidence does not clearly support a better destination. Choose "
        "destination_folder_id only from folders. Give one short reason grounded in the document's "
        "topic without quoting private content, and return no prose outside the JSON response."
    )
    return f"{instructions}\n{serialized_context}"


def organization_candidates(
    evidence: ReorganizationEvidence,
    current_parent_id: FileId | None,
    drive: DriveSummary,
    tree: DriveTree,
) -> list[ProposalDestination]:
    current_id = current_parent_id or FileId(drive.id)
    destinations = [
        ProposalDestination(
            folder_id=FileId(drive.id),
            drive_id=drive.id,
            drive_name=drive.name,
            folder_path=[drive.name],
        ),
        *_tree_destinations(drive, tree),
    ]
    title_terms = _significant_terms(evidence.file_name)
    description_terms = _significant_terms(evidence.description or "")
    content_terms = _significant_terms(evidence.content)
    destinations.sort(
        key=lambda destination: (
            destination.folder_id != current_id,
            -_destination_evidence_score(
                destination,
                title_terms=title_terms,
                description_terms=description_terms,
                content_terms=content_terms,
            ),
            tuple(part.casefold() for part in destination.folder_path),
            destination.folder_id,
        )
    )
    return destinations[:_MAX_FOLDER_CANDIDATES]


def _significant_terms(value: str) -> frozenset[str]:
    return frozenset(
        word
        for word in _WORD_PATTERN.findall(value.casefold())
        if len(word) >= _MIN_REORGANIZATION_TERM_LENGTH and word not in _REORGANIZATION_STOP_WORDS
    )


def _destination_evidence_score(
    destination: ProposalDestination,
    *,
    title_terms: frozenset[str],
    description_terms: frozenset[str],
    content_terms: frozenset[str],
) -> int:
    path_terms = _significant_terms(" ".join(destination.folder_path))
    return (
        4 * len(title_terms & path_terms)
        + 2 * len(description_terms & path_terms)
        + len(content_terms & path_terms)
    )


def _validated_reorganization(
    generated: GeneratedReorganization,
    folders: list[ProposalDestination],
    current: ProposalDestination,
) -> ReorganizationProposalRead:
    suggested = next(
        (item for item in folders if item.folder_id == generated.destination_folder_id),
        None,
    )
    if suggested is None:
        msg = "Gemini selected an unknown destination"
        raise ValueError(msg)
    if suggested.folder_id == current.folder_id:
        return ReorganizationProposalRead(
            current_destination=current,
            suggested_destination=None,
            reason=None,
        )
    return ReorganizationProposalRead(
        current_destination=current,
        suggested_destination=suggested,
        reason=generated.reason[:_MAX_REORGANIZATION_REASON_CHARS].rstrip(),
    )


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
        "Use the description's complete meaning and phrases, not an isolated overlapping word. "
        "Prefer a broad, durable folder whose purpose will remain appropriate as the file evolves; "
        "avoid a narrow or temporary folder merely because it repeats words from the description. "
        "Folders about health, relationships, identity, immigration, finance, banking, or legal "
        "matters require explicit evidence that the file is actually about that sensitive category; "
        "for example, plant health is not personal health. When placement is uncertain, keep the "
        "invoking folder. "
        "Choose destination_folder_id only from folders. Choose template_file_id only from "
        "templates, or null for a blank file. The alias must be two or three lowercase words "
        "of 2-12 ASCII letters separated by hyphens. Return one fitting emoji and no prose."
    )
    return f"{instructions}\n{serialized_context}"


def smart_create_candidates(
    request: AIMode,
    drives: list[DriveSummary],
    trees: list[DriveTree],
    *,
    candidate_depth: int | None = None,
) -> list[ProposalDestination]:
    if candidate_depth is not None and candidate_depth < 0:
        msg = "candidate_depth must be non-negative or None"
        raise ValueError(msg)
    invoking_drive = next(
        (drive for drive in drives if drive.id == request.invoking_drive_id), None
    )
    if invoking_drive is None:
        return []
    destinations = _smart_create_destinations(invoking_drive, trees)

    destinations = _destinations_within_candidate_depth(
        destinations,
        invoking_folder_id=request.invoking_folder_id,
        candidate_depth=candidate_depth,
    )
    terms = frozenset(_WORD_PATTERN.findall(request.description.casefold()))
    destinations.sort(
        key=lambda destination: (
            -_folder_score(destination, request, terms),
            tuple(part.casefold() for part in destination.folder_path),
            destination.folder_id,
        )
    )
    return destinations[:_MAX_FOLDER_CANDIDATES]


def _smart_create_destinations(
    drive: DriveSummary,
    trees: list[DriveTree],
) -> list[ProposalDestination]:
    destinations: list[ProposalDestination] = [
        ProposalDestination(
            folder_id=FileId(drive.id),
            drive_id=drive.id,
            drive_name=drive.name,
            folder_path=[drive.name],
        )
    ]
    for tree in trees:
        if tree.drive_id != drive.id:
            continue
        destinations.extend(_tree_destinations(drive, tree))
    return destinations


def _destinations_within_candidate_depth(
    destinations: list[ProposalDestination],
    *,
    invoking_folder_id: FileId | None,
    candidate_depth: int | None,
) -> list[ProposalDestination]:
    if candidate_depth is None:
        return destinations
    by_id = {destination.folder_id: destination for destination in destinations}
    invoking = by_id.get(invoking_folder_id) if invoking_folder_id is not None else None
    invoking_depth = len(invoking.folder_path) - 1 if invoking is not None else 0
    maximum_depth = invoking_depth + candidate_depth
    retained_ids: set[FileId] = set()
    if invoking is not None:
        invoking_path = tuple(part.casefold() for part in invoking.folder_path)
        retained_ids.update(
            destination.folder_id
            for destination in destinations
            if _is_path_prefix(destination.folder_path, invoking_path)
        )
    return [
        destination
        for destination in destinations
        if len(destination.folder_path) - 1 <= maximum_depth
        or destination.folder_id in retained_ids
    ]


def _is_path_prefix(path: list[str], invoking_path: tuple[str, ...]) -> bool:
    normalized = tuple(part.casefold() for part in path)
    return len(normalized) <= len(invoking_path) and invoking_path[: len(normalized)] == normalized


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
    if not _has_explicit_sensitive_destination_intent(request.description, folder):
        msg = "Gemini selected a sensitive destination without explicit evidence"
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


def _has_explicit_sensitive_destination_intent(
    description: str,
    destination: ProposalDestination,
) -> bool:
    description_terms = frozenset(_WORD_PATTERN.findall(description.casefold()))
    path_terms = frozenset(_WORD_PATTERN.findall(" ".join(destination.folder_path).casefold()))
    sensitive_categories = path_terms & _SENSITIVE_DESTINATION_INTENT_TERMS.keys()
    return not sensitive_categories or any(
        description_terms & _SENSITIVE_DESTINATION_INTENT_TERMS[category]
        for category in sensitive_categories
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
    alias = _fallback_alias(request.description)
    title_words = request.description.split()[:8]
    title = " ".join(title_words).strip().rstrip(".,:;!?")
    if not title:
        title = "New Document" if request.file_type == "document" else "New Presentation"
    return SmartProposalRead(
        name=title[:255],
        emoji="📝" if request.file_type == "document" else "📊",
        alias=alias,
        destination=destination,
        source=BlankSource(kind="blank", file_type=request.file_type),
    )


def _fallback_alias(description: str) -> str:
    words = [
        word[:12]
        for word in _WORD_PATTERN.findall(description.casefold())
        if len(word) >= _MIN_ALIAS_WORD_LENGTH
    ]
    unique_words = list(dict.fromkeys(words))
    if len(unique_words) >= _MAX_ALIAS_WORD_COUNT:
        selected = unique_words[:_MAX_ALIAS_WORD_COUNT]
        return "-".join(selected)
    if len(unique_words) >= _MIN_ALIAS_WORD_LENGTH:
        selected = unique_words[:_MIN_ALIAS_WORD_LENGTH]
        return "-".join(selected)
    if unique_words:
        return f"{unique_words[0]}-doc"
    return "new-doc"
