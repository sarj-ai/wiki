from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NewType, Protocol
import uuid

from app.document_transform.schemas import DriveFileSource
from app.identifiers import FileId


PresentationRevisionId = NewType("PresentationRevisionId", str)
DriveFileVersion = NewType("DriveFileVersion", str)


@dataclass(frozen=True, slots=True)
class PresentationFingerprints:
    semantic_sha256: str
    protected_structure_sha256: str
    brand_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PresentationSourceSnapshot:
    file_id: FileId
    title: str
    source_name: str
    parent_folder_id: FileId
    modified_time: str
    revision_id: PresentationRevisionId | None
    drive_version: DriveFileVersion
    mime_type: str
    size: int | None
    md5_checksum: str | None
    can_download: bool
    can_copy: bool
    presentation: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PresentationDraft:
    file_id: FileId
    name: str
    parent_folder_id: FileId
    revision_id: PresentationRevisionId
    source_sha256: str
    web_url: str | None = None
    applied_mutation_sha256: str | None = None
    pending_mutation_sha256: str | None = None
    mutation_base_revision_sha256: str | None = None
    pending_expected_semantic_sha256: str | None = None
    pending_expected_protected_structure_sha256: str | None = None
    pending_expected_brand_sha256: str | None = None
    completed: bool = False


@dataclass(frozen=True, slots=True)
class PresentationVerification:
    content_preserved: bool
    structure_preserved: bool
    brand_applied: bool
    semantic_sha256: str
    protected_structure_sha256: str
    brand_sha256: str | None = None


class PresentationGateway(Protocol):
    async def load_source(self, source: DriveFileSource) -> PresentationSourceSnapshot: ...

    async def source_is_unchanged(self, snapshot: PresentationSourceSnapshot) -> bool: ...

    async def recover_output(
        self,
        operation_id: uuid.UUID,
        *,
        expected_parent_folder_id: FileId,
    ) -> PresentationDraft | None: ...

    async def create_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: PresentationSourceSnapshot,
        source_sha256: str,
        output_name: str,
    ) -> PresentationDraft: ...

    async def get_presentation(self, file_id: FileId) -> dict[str, Any]: ...

    async def reconcile_pending(self, draft: PresentationDraft) -> PresentationDraft: ...

    async def apply_requests_once(
        self,
        *,
        draft: PresentationDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: PresentationRevisionId,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None = None,
    ) -> PresentationDraft: ...

    async def verify_output(
        self,
        *,
        draft: PresentationDraft,
        title: str,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None = None,
    ) -> PresentationVerification: ...

    async def complete(self, *, draft: PresentationDraft) -> None: ...
