from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Awaitable, Iterable, Iterator
from dataclasses import dataclass
from heapq import nsmallest
from itertools import chain, islice
import re
from types import MappingProxyType
from typing import Annotated, override

from google import genai
from google.genai import errors as genai_errors, types as genai_types
from loguru import logger
from pydantic import BaseModel, StringConstraints, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.drive.schemas import MIME_DOC, MIME_SLIDE, DriveNode, DriveSummary, DriveTree
from app.drive.service import DriveService
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import DriveId, FileId
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
_SENSITIVE_DESTINATION_INTENT_TERMS = MappingProxyType(
    {
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
        "legal": frozenset(
            {"attorney", "compliance", "contract", "court", "law", "legal", "lawsuit"}
        ),
        "relationships": frozenset(
            {"family", "marriage", "partner", "relationship", "spouse", "wedding"}
        ),
    }
)
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


class _ReorganizationPromptContext(BaseModel):
    current_destination: ProposalDestination
    document: ReorganizationEvidence
    folders: list[ProposalDestination]


class _ProposalPromptContext(BaseModel):
    description: str
    file_type: SmartFileType
    folders: list[ProposalDestination]
    templates: list[TemplateRead]


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
    def __init__(
        self,
        *,
        project: str | None = None,
        model: str | None = None,
        client: genai.Client | None = None,
    ) -> None:
        resolved_project = project or settings.GOOGLE_CLOUD_PROJECT
        self._model = model or settings.GEMINI_REORGANIZATION_MODEL
        self._client = (
            client
            if client is not None
            else (
                genai.Client(
                    vertexai=True,
                    project=resolved_project,
                    location=settings.GEMINI_LOCATION,
                )
                if resolved_project is not None
                else None
            )
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
        reads = await _parallel_reads(
            self._document_content(file),
            self._sidebar_cache.get_drive_tree(self._db, drive.id),
        )
        content, cached = reads.primary, reads.secondary
        evidence = ReorganizationEvidence(
            content=content,
            description=file.description,
            file_name=file.name,
            modified_time=file.modified_time,
        )
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

    async def _document_content(self, file: FileResponse) -> str:
        if file.mime_type != MIME_DOC:
            return ""
        content = await self._drive.fetch_document_text(file.id)
        if content is None:
            logger.warning("Re-organize content unavailable")
            raise ReorganizationUnavailableError
        return content


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
    def __init__(self, *, client: genai.Client | None = None) -> None:
        self._client = (
            client
            if client is not None
            else (
                genai.Client(
                    vertexai=True,
                    project=settings.GOOGLE_CLOUD_PROJECT,
                    location=settings.GEMINI_LOCATION,
                )
                if settings.GOOGLE_CLOUD_PROJECT is not None
                else None
            )
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
        reads = await _parallel_reads(
            self._load_cached_trees(
                [drive for drive in drives if drive.id == request.invoking_drive_id]
            ),
            self._templates.list_templates(),
        )
        trees, catalog = reads.primary, reads.secondary
        folders = smart_create_candidates(request, drives, trees, candidate_depth=1)
        compatible_templates = _compatible_templates(
            request.file_type, catalog.templates, drive_id=request.invoking_drive_id
        )
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


@dataclass(frozen=True, slots=True)
class _ParallelReads[Primary, Secondary]:
    primary: Primary
    secondary: Secondary


async def _parallel_reads[Primary, Secondary](
    primary: Awaitable[Primary], secondary: Awaitable[Secondary]
) -> _ParallelReads[Primary, Secondary]:
    secondary_task = asyncio.ensure_future(secondary)
    try:
        return _ParallelReads(await primary, await secondary_task)
    except BaseException:
        secondary_task.cancel()
        await asyncio.gather(secondary_task, return_exceptions=True)
        raise


def _build_reorganization_prompt(
    evidence: ReorganizationEvidence,
    current: ProposalDestination,
    folders: list[ProposalDestination],
) -> str:
    serialized_context = _ReorganizationPromptContext(
        current_destination=current, document=evidence, folders=folders
    ).model_dump_json(ensure_ascii=False)
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
    root = ProposalDestination(
        folder_id=FileId(drive.id),
        drive_id=drive.id,
        drive_name=drive.name,
        folder_path=[drive.name],
    )
    destinations: Iterable[ProposalDestination] = (
        [root, *_tree_destinations(drive, tree)]
        if len(tree.folders) <= _MAX_FOLDER_CANDIDATES
        else chain(
            (root,),
            (
                ProposalDestination(
                    folder_id=folder_id,
                    drive_id=drive.id,
                    drive_name=drive.name,
                    folder_path=path,
                )
                for folder_id, path in _tree_folder_paths(drive, tree)
            ),
        )
    )
    title_terms = _significant_terms(evidence.file_name)
    description_terms = _significant_terms(evidence.description or "")
    content_terms = _significant_terms(evidence.content)
    return nsmallest(
        _MAX_FOLDER_CANDIDATES,
        destinations,
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
        ),
    )


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
    serialized_context = _ProposalPromptContext(
        description=request.description,
        file_type=request.file_type,
        folders=folders,
        templates=templates,
    ).model_dump_json(ensure_ascii=False)
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
    destinations = (
        _smart_create_destinations(invoking_drive, trees)
        if candidate_depth is None
        else _smart_create_bounded_destinations(
            invoking_drive,
            trees,
            invoking_folder_id=request.invoking_folder_id,
            candidate_depth=candidate_depth,
        )
    )
    terms = frozenset(_WORD_PATTERN.findall(request.description.casefold()))
    return nsmallest(
        _MAX_FOLDER_CANDIDATES,
        destinations,
        key=lambda destination: (
            -_folder_score(destination, request, terms),
            tuple(part.casefold() for part in destination.folder_path),
            destination.folder_id,
        ),
    )


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


def _smart_create_bounded_destinations(
    drive: DriveSummary,
    trees: list[DriveTree],
    *,
    invoking_folder_id: FileId | None,
    candidate_depth: int,
) -> list[ProposalDestination]:
    paths = chain(
        [(FileId(drive.id), [drive.name])],
        (
            item
            for tree in trees
            if tree.drive_id == drive.id
            for item in _tree_folder_paths(drive, tree)
        ),
    )
    retained = (
        ((folder_id, path) for folder_id, path in paths if len(path) - 1 <= candidate_depth)
        if invoking_folder_id is None
        else _paths_within_candidate_depth(
            list(paths), invoking_folder_id=invoking_folder_id, candidate_depth=candidate_depth
        )
    )
    return [
        ProposalDestination(
            folder_id=folder_id,
            drive_id=drive.id,
            drive_name=drive.name,
            folder_path=path,
        )
        for folder_id, path in retained
    ]


def _paths_within_candidate_depth(
    paths: list[tuple[FileId, list[str]]],
    *,
    invoking_folder_id: FileId,
    candidate_depth: int,
) -> list[tuple[FileId, list[str]]]:
    by_id = dict(paths)
    invoking = by_id.get(invoking_folder_id)
    invoking_depth = len(invoking) - 1 if invoking is not None else 0
    maximum_depth = invoking_depth + candidate_depth
    retained_ids: set[FileId] = set()
    if invoking is not None:
        invoking_path = tuple(part.casefold() for part in invoking)
        retained_ids.update(
            folder_id for folder_id, path in paths if _is_path_prefix(path, invoking_path)
        )
    return [
        (folder_id, path)
        for folder_id, path in paths
        if len(path) - 1 <= maximum_depth or folder_id in retained_ids
    ]


def _is_path_prefix(path: list[str], invoking_path: tuple[str, ...]) -> bool:
    if len(path) > len(invoking_path):
        return False
    normalized = tuple(part.casefold() for part in path)
    return invoking_path[: len(normalized)] == normalized


def _tree_destinations(drive: DriveSummary, tree: DriveTree) -> list[ProposalDestination]:
    return [
        ProposalDestination(
            folder_id=folder_id,
            drive_id=drive.id,
            drive_name=drive.name,
            folder_path=path,
        )
        for folder_id, path in _tree_folder_paths(drive, tree)
    ]


def _tree_folder_paths(drive: DriveSummary, tree: DriveTree) -> Iterator[tuple[FileId, list[str]]]:
    by_id = {folder.id: folder for folder in tree.folders}
    parent_ids = {folder.parent_id for folder in tree.folders if folder.parent_id is not None}
    paths: dict[FileId, list[str]] = {}
    for folder in tree.folders:
        if folder.parent_id is None or folder.parent_id not in by_id:
            yield folder.id, [drive.name, folder.name]
        else:
            ancestry = _memoized_folder_ancestry(folder, by_id, parent_ids=parent_ids, paths=paths)
            yield folder.id, [drive.name, *ancestry]


def _memoized_folder_ancestry(
    folder: DriveNode,
    by_id: dict[FileId, DriveNode],
    *,
    parent_ids: set[FileId],
    paths: dict[FileId, list[str]],
) -> list[str]:
    if folder is not by_id[folder.id]:
        return _folder_ancestry(folder, by_id)
    pending: list[DriveNode] = []
    visited: set[FileId] = set()
    current: DriveNode | None = folder
    while current is not None and current.id not in paths:
        if current.id in visited:
            break
        pending.append(current)
        visited.add(current.id)
        current = by_id.get(current.parent_id) if current.parent_id is not None else None
    if current is not None and current.id in visited:
        return _folder_ancestry(folder, by_id)
    path = paths[current.id] if current is not None else []
    for ancestor in reversed(pending):
        path = [*path, ancestor.name]
        if ancestor.id in parent_ids:
            paths[ancestor.id] = path
    return path


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
    *,
    drive_id: DriveId | None,
) -> list[TemplateRead]:
    mime_type = MIME_DOC if file_type == "document" else MIME_SLIDE
    return list(
        islice(
            (
                template
                for template in templates
                if template.mime_type == mime_type and template.drive_id == drive_id
            ),
            _MAX_TEMPLATE_CANDIDATES,
        )
    )


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
    path_terms = frozenset(_WORD_PATTERN.findall(" ".join(destination.folder_path).casefold()))
    sensitive_categories = path_terms & _SENSITIVE_DESTINATION_INTENT_TERMS.keys()
    if not sensitive_categories:
        return True
    description_terms = frozenset(_WORD_PATTERN.findall(description.casefold()))
    return any(
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
