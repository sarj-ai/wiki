from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol
import uuid

from app.document_transform.custom_furniture import CustomFurnitureBinding, CustomHeaderTitleTarget
from app.document_transform.header_provenance import HeaderIdentityEvidence
from app.document_transform.ir import DocumentIR
from app.document_transform.native_date_flow import NativeDateCheckpoint
from app.document_transform.native_footer_flow import NativeFooterCheckpoint
from app.document_transform.native_table_font import TableFontPlan
from app.document_transform.schemas import (
    DocumentOutput,
    DriveFileSource,
    MarkdownSource,
    TabNamingCheckpoint,
)
from app.document_transform.tab_structure_execution import StructureProvider
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, DriveId, FileId


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    title: str
    parent_folder_id: FileId
    modified_time: str | None
    revision_id: DocsRevisionId | None
    media_type: str
    markdown: str | None = None
    google_document: dict[str, Any] | None = None
    owned_by_me: bool = False
    can_edit: bool = False
    is_personal_drive: bool = False
    shared_drive_id: DriveId | None = None
    web_url: str | None = None


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
    result_sha256: str | None = None
    verified_revision_sha256: str | None = None
    native_footer: NativeFooterCheckpoint | None = None
    native_dates: NativeDateCheckpoint | None = None


@dataclass(frozen=True, slots=True)
class OutputVerification:
    content_preserved: bool
    structure_preserved: bool
    brand_applied: bool = True
    revision_id: DocsRevisionId | None = None
    supported_brand_applied: bool = False


@dataclass(frozen=True, slots=True)
class InPlaceInspection:
    output: DocumentOutput
    headers: tuple[TabHeader, ...]
    missing_header_tab_ids: tuple[DocsTabId, ...]
    unknown_header_tab_ids: tuple[DocsTabId, ...]
    marker_range: dict[str, Any] | None
    logo_uri: str
    structural_sha256: str
    protected_structure_sha256: str
    body_sha256: str
    header_safe_sha256: str
    preserved_headers: tuple[TabHeader, ...] = ()
    fully_branded: bool = False
    header_identity: HeaderIdentityEvidence | None = None
    custom_furniture: tuple[CustomFurnitureBinding, ...] = ()
    custom_header_title_targets: tuple[CustomHeaderTitleTarget, ...] = ()
    custom_header_titles_present: tuple[CustomHeaderTitleTarget, ...] = ()
    supported_brand_applied: bool = False


@dataclass(frozen=True, slots=True)
class InPlaceStageResult:
    revision_id: DocsRevisionId
    document: dict[str, Any]


class HeaderIdentityCapture(Protocol):
    async def __call__(
        self,
        *,
        draft: OutputDraft,
        operation_id: uuid.UUID,
        managed_headers: dict[str, str],
        title: str,
    ) -> HeaderIdentityEvidence | None: ...


class DocumentGateway(ABC):
    supports_native_footer = False
    supports_native_dates = False
    structure_provider: StructureProvider | None = None
    capture_header_identity: HeaderIdentityCapture | None = None

    async def plan_copy_dates(
        self, *, draft: OutputDraft, operation_id: uuid.UUID
    ) -> NativeDateCheckpoint | None:
        raise NotImplementedError

    async def apply_copy_dates(
        self, *, document_id: FileId, checkpoint: NativeDateCheckpoint
    ) -> NativeDateCheckpoint:
        raise NotImplementedError

    async def plan_copy_footer(
        self, *, draft: OutputDraft, operation_id: uuid.UUID
    ) -> NativeFooterCheckpoint | None:
        raise NotImplementedError

    async def apply_copy_footer_stage(
        self, *, document_id: FileId, checkpoint: NativeFooterCheckpoint
    ) -> NativeFooterCheckpoint:
        raise NotImplementedError

    async def plan_tab_naming(
        self,
        *,
        document_id: FileId,
        title: str,
        operation_id: uuid.UUID,
        required_revision_id: DocsRevisionId,
        prefer_document_title: bool = False,
    ) -> TabNamingCheckpoint | None:
        raise NotImplementedError

    async def apply_tab_naming(
        self,
        *,
        document_id: FileId,
        checkpoint: TabNamingCheckpoint,
    ) -> DocsRevisionId:
        raise NotImplementedError

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
        expected_parent_folder_id: FileId,
    ) -> bool: ...

    async def inspect_in_place(
        self,
        *,
        snapshot: SourceSnapshot,
        title: str,
        table_font_plan: TableFontPlan | None = None,
    ) -> InPlaceInspection:
        raise NotImplementedError

    async def apply_in_place_stage(
        self,
        *,
        document_id: FileId,
        required_revision_id: DocsRevisionId,
        marker_name: str,
        marker_range: dict[str, Any],
        requests: list[dict[str, Any]],
    ) -> InPlaceStageResult:
        raise NotImplementedError

    async def verify_in_place(
        self,
        *,
        document: dict[str, Any],
        title: str,
        expected: DocumentIR,
        structural_sha256: str,
        protected_structure_sha256: str,
        verify_brand: bool,
        custom_furniture: tuple[CustomFurnitureBinding, ...] = (),
        custom_header_titles: tuple[CustomHeaderTitleTarget, ...] = (),
        table_font_plan: TableFontPlan | None = None,
    ) -> OutputVerification:
        raise NotImplementedError

    @abstractmethod
    async def create_from_drive_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
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
        managed_headers: dict[str, str] | None = None,
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
    async def verify_output(
        self,
        *,
        draft: OutputDraft,
        source: SourceSnapshot,
        expected: DocumentIR,
        verify_brand: bool,
        header_title: str | None = None,
    ) -> OutputVerification: ...

    @abstractmethod
    async def complete(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        result: dict[str, Any],
    ) -> None: ...
