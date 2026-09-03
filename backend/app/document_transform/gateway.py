from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
import uuid

from app.document_transform.schemas import DocumentOutput, DriveFileSource, MarkdownSource
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    title: str
    parent_folder_id: FileId
    modified_time: str | None
    revision_id: DocsRevisionId | None
    media_type: str
    markdown: str | None = None
    google_document: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TabHeader:
    tab_id: DocsTabId
    header_id: DocsSegmentId


@dataclass(frozen=True, slots=True)
class OutputDraft:
    output: DocumentOutput
    document_id: FileId
    tab_id: DocsTabId
    revision_id: DocsRevisionId
    source_sha256: str
    headers: tuple[TabHeader, ...] = ()
    logo_uri: str | None = None
    applied_mutation_sha256: str | None = None
    pending_mutation_sha256: str | None = None
    mutation_base_revision_sha256: str | None = None
    brand_warnings: tuple[str, ...] = ()
    completed: bool = False
    result: dict[str, Any] | None = None


class DocumentGateway(ABC):
    @abstractmethod
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot: ...

    @abstractmethod
    async def recover_output(self, operation_id: uuid.UUID) -> OutputDraft | None: ...

    @abstractmethod
    async def source_is_unchanged(
        self,
        source: DriveFileSource,
        *,
        expected_modified_time: str,
        expected_revision_id: DocsRevisionId | None,
    ) -> bool: ...

    @abstractmethod
    async def create_from_drive_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
    ) -> OutputDraft: ...

    @abstractmethod
    async def create_from_drive_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
        imported_docx: bytes | None = None,
    ) -> OutputDraft: ...

    @abstractmethod
    async def create_from_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: MarkdownSource,
        source_sha256: str,
        output_name: str,
        imported_docx: bytes | None = None,
    ) -> OutputDraft: ...

    @abstractmethod
    async def prepare_brand_shell(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
    ) -> OutputDraft:
        """Create or recover header and footer segments and resolve the logo URI."""

    @abstractmethod
    async def apply_requests_once(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: DocsRevisionId,
    ) -> OutputDraft: ...

    @abstractmethod
    async def complete(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        result: dict[str, Any],
    ) -> None: ...
