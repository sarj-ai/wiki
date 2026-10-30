from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import hashlib
from itertools import batched
import json
from typing import Literal, Protocol
import uuid

from googleapiclient.errors import HttpError
import httplib2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.document_transform.qa_contract import (
    QA_MAX_FOLDER_LIST_PAGES,
    QA_MAX_TRAVERSAL_METADATA_BYTES,
    QA_MAX_TRAVERSED_FOLDERS,
)
from app.document_transform.qa_grants import QaAuthority
from app.document_transform.word_gateway import WORD_MIME
from app.drive.errors import DriveTraversalIncompleteError
from app.drive.schemas import (
    MIME_DOC,
    MIME_FOLDER,
    MIME_SHORTCUT,
    BoundedFolderMembers,
    DriveNode,
)
from app.files.errors import FilesError
from app.files.schemas import FileResponse
from app.identifiers import DriveId, FileId, QaPlanId


_MAX_CORPUS_ITEMS = 100_000
_FILE_METADATA_BATCH_SIZE = 100


class QaCorpusPlanError(Exception):
    pass


class UnsupportedCorpusReason(StrEnum):
    SHORTCUT = "shortcut"
    UNSUPPORTED_MIME_TYPE = "unsupported_mime_type"
    INVALID_DOCX_NAME = "invalid_docx_name"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QaCorpusDocument(_StrictModel):
    file_id: FileId = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(
        pattern=(
            r"^(?:application/vnd\.google-apps\.document|"
            r"application/vnd\.openxmlformats-officedocument\.wordprocessingml\.document)$"
        )
    )
    kind: Literal["native_google_doc", "docx"]
    parent_folder_id: FileId = Field(min_length=1, max_length=255)
    drive_id: DriveId
    modified_time: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    supported_modes: tuple[Literal["format", "unslop", "both"], ...]


class QaCorpusUnsupportedFile(_StrictModel):
    file_id: FileId = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    parent_folder_id: FileId = Field(min_length=1, max_length=255)
    drive_id: DriveId
    version: str = Field(min_length=1, max_length=255)
    reason: UnsupportedCorpusReason


class QaCorpusSourceFolder(_StrictModel):
    file_id: FileId = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    mime_type: Literal["application/vnd.google-apps.folder"]
    parent_folder_id: FileId | None
    drive_id: DriveId
    modified_time: str | None
    version: str = Field(min_length=1, max_length=255)


class QaCorpusMemberVersion(_StrictModel):
    file_id: FileId = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)


class QaCorpusPlan(_StrictModel):
    schema_version: Literal[1] = 1
    plan_id: QaPlanId
    source_folder_id: FileId = Field(min_length=1, max_length=255)
    source_drive_id: DriveId
    source_folder: QaCorpusSourceFolder
    membership_scope: Literal["recursive_descendants"] = "recursive_descendants"
    documents: tuple[QaCorpusDocument, ...]
    unsupported_files: tuple[QaCorpusUnsupportedFile, ...]
    member_versions: tuple[QaCorpusMemberVersion, ...]
    observed_item_count: int = Field(ge=0)
    traversed_folder_count: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class _CorpusListing:
    members: tuple[DriveNode, ...]
    traversed_folder_count: int
    page_count: int
    metadata_bytes: int


@dataclass(frozen=True, slots=True)
class _ClassifiedCorpus:
    documents: tuple[QaCorpusDocument, ...]
    unsupported_files: tuple[QaCorpusUnsupportedFile, ...]


@dataclass(frozen=True, slots=True)
class _RefreshedCorpus:
    members: tuple[FileResponse, ...]
    metadata_bytes: int


class QaCorpusDriveReader(Protocol):
    async def list_folder_members_bounded(
        self,
        folder_id: FileId,
        *,
        drive_id: DriveId,
        max_pages: int,
        max_children: int,
        max_metadata_bytes: int,
    ) -> BoundedFolderMembers: ...


class QaCorpusFileReader(Protocol):
    async def get(self, file_id: FileId) -> FileResponse: ...

    async def get_qa_corpus_metadata_many(
        self, file_ids: list[FileId]
    ) -> dict[FileId, FileResponse]: ...


async def plan_qa_corpus(
    *,
    source_folder_id: FileId,
    drive: QaCorpusDriveReader,
    files: QaCorpusFileReader,
) -> QaCorpusPlan:
    try:
        return await _plan_qa_corpus(
            source_folder_id=source_folder_id,
            drive=drive,
            files=files,
        )
    except QaCorpusPlanError:
        raise
    except (
        ConnectionError,
        DriveTraversalIncompleteError,
        FilesError,
        HttpError,
        TimeoutError,
        httplib2.ServerNotFoundError,
    ) as exc:
        raise QaCorpusPlanError from exc


async def _plan_qa_corpus(
    *,
    source_folder_id: FileId,
    drive: QaCorpusDriveReader,
    files: QaCorpusFileReader,
) -> QaCorpusPlan:
    source_before = await files.get(source_folder_id)
    _require_source_folder(source_before, source_folder_id)
    source_drive_id = _required_drive(source_before)
    try:
        first = await _list_complete_members(
            drive,
            source_folder_id,
            source_drive_id,
            max_pages=QA_MAX_FOLDER_LIST_PAGES,
            max_metadata_bytes=QA_MAX_TRAVERSAL_METADATA_BYTES,
        )
        refreshed_result = await _refresh_all_members(
            files,
            first.members,
            max_metadata_bytes=QA_MAX_TRAVERSAL_METADATA_BYTES - first.metadata_bytes,
        )
        refreshed = refreshed_result.members
        _require_refreshed_members(first.members, refreshed, source_drive_id)
        second = await _list_complete_members(
            drive,
            source_folder_id,
            source_drive_id,
            max_pages=QA_MAX_FOLDER_LIST_PAGES - first.page_count,
            max_metadata_bytes=(
                QA_MAX_TRAVERSAL_METADATA_BYTES
                - first.metadata_bytes
                - refreshed_result.metadata_bytes
            ),
        )
    except DriveTraversalIncompleteError as exc:
        raise QaCorpusPlanError from exc
    if (
        _node_fingerprints(first.members) != _node_fingerprints(second.members)
        or first.traversed_folder_count != second.traversed_folder_count
    ):
        raise QaCorpusPlanError
    source_after = await files.get(source_folder_id)
    _require_source_folder(source_after, source_folder_id)
    if _source_fingerprint(source_after) != _source_fingerprint(source_before):
        raise QaCorpusPlanError
    source = _source_model(source_after)
    classified = _classify(refreshed)
    try:
        plan = QaCorpusPlan(
            plan_id=QaPlanId(_plan_uuid(source, first.members, refreshed)),
            source_folder_id=source_folder_id,
            source_drive_id=source.drive_id,
            source_folder=source,
            documents=classified.documents,
            unsupported_files=classified.unsupported_files,
            member_versions=tuple(
                QaCorpusMemberVersion(file_id=member.id, version=_required_version(member))
                for member in refreshed
            ),
            observed_item_count=len(refreshed),
            traversed_folder_count=second.traversed_folder_count,
        )
    except ValidationError as exc:
        raise QaCorpusPlanError from exc
    _require_size(plan.model_dump(mode="json"))
    return plan


async def plan_authorized_qa_corpus(
    authority: QaAuthority,
    *,
    drive: QaCorpusDriveReader,
    files: QaCorpusFileReader,
) -> QaCorpusPlan:
    return await plan_qa_corpus(
        source_folder_id=authority.source_folder_id,
        drive=drive,
        files=files,
    )


async def _list_complete_members(
    drive: QaCorpusDriveReader,
    source_folder_id: FileId,
    source_drive_id: DriveId,
    *,
    max_pages: int,
    max_metadata_bytes: int,
) -> _CorpusListing:
    if max_pages < 1 or max_metadata_bytes < 1:
        raise QaCorpusPlanError
    pending = [source_folder_id]
    visited_folders: set[FileId] = set()
    seen_items: set[FileId] = set()
    members: list[DriveNode] = []
    page_count = 0
    metadata_bytes = 0
    while pending:
        folder_id = pending.pop(0)
        if folder_id in visited_folders or len(visited_folders) >= QA_MAX_TRAVERSED_FOLDERS:
            raise QaCorpusPlanError
        visited_folders.add(folder_id)
        remaining_pages = max_pages - page_count
        remaining_items = _MAX_CORPUS_ITEMS - len(members)
        remaining_metadata_bytes = max_metadata_bytes - metadata_bytes
        if remaining_pages < 1 or remaining_items < 1 or remaining_metadata_bytes < 1:
            raise QaCorpusPlanError
        result = await drive.list_folder_members_bounded(
            folder_id,
            drive_id=source_drive_id,
            max_pages=remaining_pages,
            max_children=remaining_items,
            max_metadata_bytes=remaining_metadata_bytes,
        )
        if len(result.members) > remaining_items:
            raise QaCorpusPlanError
        page_count += result.page_count
        metadata_bytes += result.metadata_bytes
        if page_count > max_pages or metadata_bytes > max_metadata_bytes:
            raise QaCorpusPlanError
        for member in sorted(result.members, key=lambda item: str(item.id)):
            if (
                member.parent_id != folder_id
                or member.drive_id != source_drive_id
                or member.id == source_folder_id
                or member.id in seen_items
            ):
                raise QaCorpusPlanError
            seen_items.add(member.id)
            members.append(member)
            if member.mime_type == MIME_FOLDER:
                pending.append(member.id)
    ordered = tuple(sorted(members, key=lambda member: str(member.id)))
    _require_size([_node_fingerprint(member) for member in ordered])
    return _CorpusListing(
        members=ordered,
        traversed_folder_count=len(visited_folders),
        page_count=page_count,
        metadata_bytes=metadata_bytes,
    )


async def _refresh_all_members(
    files: QaCorpusFileReader,
    members: tuple[DriveNode, ...],
    *,
    max_metadata_bytes: int,
) -> _RefreshedCorpus:
    if max_metadata_bytes < 1:
        raise QaCorpusPlanError
    refreshed: dict[FileId, FileResponse] = {}
    metadata_bytes = 0
    for chunk in batched(
        (member.id for member in members), _FILE_METADATA_BATCH_SIZE, strict=False
    ):
        loaded = await files.get_qa_corpus_metadata_many(list(chunk))
        if not set(loaded).isdisjoint(refreshed):
            raise QaCorpusPlanError
        for file_id, member in loaded.items():
            metadata_bytes += len(_canonical_json(_file_fingerprint(member)))
            if metadata_bytes > max_metadata_bytes:
                raise QaCorpusPlanError
            refreshed[file_id] = member
    expected = {member.id for member in members}
    if set(refreshed) != expected:
        raise QaCorpusPlanError
    return _RefreshedCorpus(
        members=tuple(refreshed[file_id] for file_id in sorted(refreshed, key=str)),
        metadata_bytes=metadata_bytes,
    )


def _require_refreshed_members(
    listed: tuple[DriveNode, ...],
    refreshed: tuple[FileResponse, ...],
    source_drive_id: DriveId,
) -> None:
    listed_by_id = {member.id: member for member in listed}
    for member in refreshed:
        original = listed_by_id.get(member.id)
        if original is None:
            raise QaCorpusPlanError
        listed_fingerprint = (
            original.name,
            original.mime_type,
            original.parent_id,
            original.modified_time,
            original.version,
            original.drive_id,
        )
        refreshed_fingerprint = (
            member.name,
            member.mime_type,
            member.parent_folder_id,
            member.modified_time,
            member.version,
            member.drive_id,
        )
        if listed_fingerprint != refreshed_fingerprint or member.drive_id != source_drive_id:
            raise QaCorpusPlanError


def _classify(
    members: tuple[FileResponse, ...],
) -> _ClassifiedCorpus:
    documents: list[QaCorpusDocument] = []
    unsupported: list[QaCorpusUnsupportedFile] = []
    for member in members:
        if member.mime_type == MIME_FOLDER:
            continue
        reason = _unsupported_reason(member)
        if reason is not None:
            unsupported.append(
                QaCorpusUnsupportedFile(
                    file_id=member.id,
                    name=member.name,
                    mime_type=member.mime_type,
                    parent_folder_id=_required_parent(member),
                    drive_id=_required_drive(member),
                    version=_required_version(member),
                    reason=reason,
                )
            )
            continue
        if member.modified_time is None:
            raise QaCorpusPlanError
        native = member.mime_type == MIME_DOC
        documents.append(
            QaCorpusDocument(
                file_id=member.id,
                name=member.name,
                mime_type=member.mime_type,
                kind="native_google_doc" if native else "docx",
                parent_folder_id=_required_parent(member),
                drive_id=_required_drive(member),
                modified_time=member.modified_time,
                version=_required_version(member),
                supported_modes=("format", "unslop", "both") if native else ("format",),
            )
        )
    return _ClassifiedCorpus(tuple(documents), tuple(unsupported))


def _unsupported_reason(member: FileResponse) -> UnsupportedCorpusReason | None:
    if member.mime_type == MIME_SHORTCUT:
        return UnsupportedCorpusReason.SHORTCUT
    if member.mime_type == WORD_MIME:
        return (
            None
            if member.name.casefold().endswith(".docx")
            else UnsupportedCorpusReason.INVALID_DOCX_NAME
        )
    if member.mime_type != MIME_DOC:
        return UnsupportedCorpusReason.UNSUPPORTED_MIME_TYPE
    return None


def _required_parent(member: FileResponse) -> FileId:
    if member.parent_folder_id is None:
        raise QaCorpusPlanError
    return member.parent_folder_id


def _require_source_folder(folder: FileResponse, expected_id: FileId) -> None:
    if (
        folder.id != expected_id
        or folder.mime_type != MIME_FOLDER
        or folder.drive_id is None
        or folder.version is None
        or not folder.version
    ):
        raise QaCorpusPlanError


def _required_drive(member: FileResponse) -> DriveId:
    if member.drive_id is None:
        raise QaCorpusPlanError
    return member.drive_id


def _required_version(member: FileResponse) -> str:
    if member.version is None or not member.version:
        raise QaCorpusPlanError
    return member.version


def _source_model(folder: FileResponse) -> QaCorpusSourceFolder:
    try:
        return QaCorpusSourceFolder(
            file_id=folder.id,
            name=folder.name,
            mime_type="application/vnd.google-apps.folder",
            parent_folder_id=folder.parent_folder_id,
            drive_id=_required_drive(folder),
            modified_time=folder.modified_time,
            version=_required_version(folder),
        )
    except ValidationError as exc:
        raise QaCorpusPlanError from exc


def _source_fingerprint(folder: FileResponse) -> tuple[str, ...]:
    return (
        str(folder.id),
        folder.name,
        folder.mime_type,
        str(folder.parent_folder_id or ""),
        str(folder.drive_id or ""),
        folder.modified_time or "",
        folder.version or "",
    )


def _node_fingerprints(members: Iterable[DriveNode]) -> tuple[tuple[str, ...], ...]:
    return tuple(_node_fingerprint(member) for member in members)


def _node_fingerprint(member: DriveNode) -> tuple[str, ...]:
    return (
        str(member.id),
        member.name,
        member.mime_type,
        str(member.parent_id or ""),
        str(member.drive_id or ""),
        member.modified_time or "",
        member.version or "",
        str(member.shortcut_target_id or ""),
        member.shortcut_target_mime_type or "",
    )


def _file_fingerprint(member: FileResponse) -> tuple[str, ...]:
    return (
        str(member.id),
        member.name,
        member.mime_type,
        str(member.parent_folder_id or ""),
        str(member.drive_id or ""),
        member.modified_time or "",
        member.version or "",
    )


def _plan_uuid(
    source_folder: QaCorpusSourceFolder,
    listed_members: tuple[DriveNode, ...],
    members: tuple[FileResponse, ...],
) -> uuid.UUID:
    payload = {
        "source_folder": source_folder.model_dump(mode="json"),
        "listed_members": [_node_fingerprint(member) for member in listed_members],
        "refreshed_members": [
            {
                "id": member.id,
                "name": member.name,
                "mime_type": member.mime_type,
                "parent_folder_id": member.parent_folder_id,
                "drive_id": member.drive_id,
                "modified_time": member.modified_time,
                "version": member.version,
            }
            for member in members
        ],
    }
    digest = bytearray(hashlib.sha256(_canonical_json(payload)).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x50
    digest[8] = (digest[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(digest))


def _require_size(value: object) -> None:
    if len(_canonical_json(value)) > QA_MAX_TRAVERSAL_METADATA_BYTES:
        raise QaCorpusPlanError


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
