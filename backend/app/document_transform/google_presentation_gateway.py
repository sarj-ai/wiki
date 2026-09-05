from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from functools import cache
import hashlib
from http import HTTPStatus
from io import BytesIO
import json
from pathlib import PurePosixPath
import stat
import threading
from typing import Any, Protocol, override, runtime_checkable
from urllib.parse import urlsplit
import uuid
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo
import zlib

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from googleapiclient import discovery
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest, MediaIoBaseUpload

from app.document_transform.errors import (
    DocumentGatewayError,
    IncompatiblePendingOutputError,
    OutputConflictError,
    SourceChangedError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.presentation_gateway import (
    DriveFileVersion,
    PresentationDraft,
    PresentationFingerprints,
    PresentationGateway,
    PresentationRevisionId,
    PresentationSourceSnapshot,
    PresentationVerification,
)
from app.document_transform.schemas import DriveFileSource
from app.document_transform.slides_ir import parse_google_presentation
from app.drive.retry import ProviderDeadlineError, execute_mutation, execute_with_retry
from app.drive.schemas import MIME_FOLDER, MIME_SLIDE
from app.drive.service import GoogleRefreshHandler, build_google_credentials, reuse_drive_client
from app.files.errors import FileNotInScopeError, FilesError
from app.files.schemas import FileResponse
from app.files.service import FilesService
from app.identifiers import FileId
from app.models.user import User


PPTX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_MAX_PPTX_BYTES = 25 * 1024 * 1024
_MAX_ZIP_ENTRIES = 10_000
_MAX_ZIP_ENTRY_BYTES = 50 * 1024 * 1024
_MAX_ZIP_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
_FORBIDDEN_PPTX_PATH_COMPONENTS = frozenset(
    {
        "activex",
        "embeddings",
        "oleobjects",
        "vbaproject.bin",
    }
)
_FORBIDDEN_CONTENT_TYPE_MARKERS = (
    "macroenabled",
    "vbaproject",
    "activex",
    "oleobject",
)
_TERMINAL_SCOPE_ERROR_MARKERS = (
    b"access_token_scope_insufficient",
    b"insufficient authentication scopes",
    b"insufficientfilepermissions",
    b"insufficientpermissions",
)
_MAX_RELATIONSHIP_BYTES = 1 * 1024 * 1024
_MAX_CONTENT_TYPES_BYTES = 1 * 1024 * 1024
_MAX_PRESENTATION_XML_BYTES = 5 * 1024 * 1024
_MAX_HYPERLINK_TARGET_LENGTH = 2_048
_ASCII_CONTROL_LIMIT = 32
_ALLOWED_EXTERNAL_HYPERLINK_SCHEMES = frozenset({"http", "https", "mailto", "tel"})
_ALLOWED_EXTERNAL_HYPERLINK_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/hyperlink",
    }
)

_PROP_OPERATION = "sarjTransformOperation"
_PROP_SOURCE_HASH = "sarjTransformSourceHash"
_PROP_STATE = "sarjTransformState"
_PROP_MUTATION = "sarjTransformMutation"
_PROP_PENDING_MUTATION = "sarjTransformPendingMutation"
_PROP_MUTATION_BASE_REVISION_HASH = "sarjTransformMutationBaseHash"
_PROP_EXPECTED_SEMANTIC_HASH = "sarjTransformExpectedSemanticHash"
_PROP_EXPECTED_PROTECTED_HASH = "sarjTransformExpectedProtectedHash"
_PROP_EXPECTED_BRAND_HASH = "sarjTransformExpectedBrandHash"
_NO_BRAND_EXPECTED = "none"
_STATE_INCOMPLETE = "incomplete"
_STATE_COMPLETE = "complete"

PresentationFingerprint = Callable[[dict[str, Any], str], PresentationFingerprints]


@runtime_checkable
class _CallableBoundary(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


@runtime_checkable
class _DriveProvider(Protocol):
    def files(self) -> object: ...

    def about(self) -> object: ...


@runtime_checkable
class _SlidesProvider(Protocol):
    def presentations(self) -> object: ...


class _XmlElement(Protocol):
    tag: str
    attrib: dict[str, str]

    def iter(self) -> Iterator[_XmlElement]: ...


def _member(owner: object, name: str) -> object:
    member = getattr(owner, name, None)
    if member is None:
        message = f"{type(owner).__name__} has no {name} member"
        raise TypeError(message)
    return member


def _call(candidate: object, *args: object, **kwargs: object) -> object:
    if not isinstance(candidate, _CallableBoundary):
        message = "External API boundary is not callable"
        raise TypeError(message)
    return candidate(*args, **kwargs)


def _request(candidate: object, **kwargs: object) -> HttpRequest:
    request = _call(candidate, **kwargs)
    if not isinstance(request, HttpRequest):
        message = "Google API method did not return an HTTP request"
        raise TypeError(message)
    return request


class GooglePresentationGateway(PresentationGateway):
    def __init__(
        self,
        *,
        user: User,
        files: FilesService,
        refresh_handler: GoogleRefreshHandler | None = None,
        fingerprint: PresentationFingerprint | None = None,
        drive_provider: _DriveProvider | None = None,
        slides_provider: _SlidesProvider | None = None,
    ) -> None:
        self._user = user
        self._files = files
        self._refresh_handler = refresh_handler
        self._fingerprint = fingerprint or _default_fingerprint
        self._drive_provider = drive_provider
        self._slides_provider = slides_provider
        self._drive_clients: dict[int, _DriveProvider] = {}
        self._slides_clients: dict[int, _SlidesProvider] = {}

    @override
    async def load_source(self, source: DriveFileSource) -> PresentationSourceSnapshot:
        try:
            file = await self._files.get(source.file_id)
        except FileNotInScopeError as exc:
            raise UnsupportedSourceError from exc
        except FilesError as exc:
            if exc.http_status in {401, 403}:
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc
        if (
            file.parent_folder_id is None
            or file.modified_time is None
            or file.mime_type not in {MIME_SLIDE, PPTX_MIME_TYPE}
        ):
            raise UnsupportedSourceError
        if file.modified_time != source.expected_modified_time:
            raise SourceChangedError
        metadata = await asyncio.to_thread(self._files_get_sync, source.file_id)
        _validate_scoped_metadata(file, metadata)
        mime_type = _required_string(metadata, "mimeType")
        capabilities = _required_capabilities(metadata)
        can_download = capabilities.get("canDownload") is True
        can_copy = capabilities.get("canCopy") is True
        size = _optional_nonnegative_int(metadata, "size")
        md5_checksum = _optional_string(metadata, "md5Checksum")
        _validate_source_kind(
            name=_required_string(metadata, "name"),
            mime_type=mime_type,
            size=size,
            md5_checksum=md5_checksum,
            can_download=can_download,
            can_copy=can_copy,
        )
        presentation = (
            await asyncio.to_thread(self._get_presentation_sync, source.file_id)
            if mime_type == MIME_SLIDE
            else None
        )
        revision_id = (
            PresentationRevisionId(_required_string(presentation, "revisionId"))
            if presentation is not None
            else None
        )
        name = _required_string(metadata, "name")
        return PresentationSourceSnapshot(
            file_id=source.file_id,
            title=name[:-5] if mime_type == PPTX_MIME_TYPE else name,
            source_name=name,
            parent_folder_id=file.parent_folder_id,
            modified_time=file.modified_time,
            revision_id=revision_id,
            drive_version=DriveFileVersion(_required_string(metadata, "version")),
            mime_type=mime_type,
            size=size,
            md5_checksum=md5_checksum,
            can_download=can_download,
            can_copy=can_copy,
            presentation=presentation,
        )

    @override
    async def source_is_unchanged(self, snapshot: PresentationSourceSnapshot) -> bool:
        try:
            file = await self._files.get(snapshot.file_id)
        except FileNotInScopeError:
            return False
        except FilesError as exc:
            if exc.http_status in {401, 403}:
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc
        if (
            file.mime_type != snapshot.mime_type
            or file.parent_folder_id != snapshot.parent_folder_id
            or file.modified_time != snapshot.modified_time
        ):
            return False
        return await asyncio.to_thread(self._source_is_unchanged_sync, snapshot)

    @override
    async def recover_output(
        self,
        operation_id: uuid.UUID,
        *,
        expected_parent_folder_id: FileId,
    ) -> PresentationDraft | None:
        if (draft := await asyncio.to_thread(self._recover_output_sync, operation_id)) is None:
            return None
        await self._validate_output_scope(draft, expected_parent_folder_id)
        return draft

    @override
    async def create_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: PresentationSourceSnapshot,
        source_sha256: str,
        output_name: str,
    ) -> PresentationDraft:
        if not await self.source_is_unchanged(source):
            raise SourceChangedError
        name = await asyncio.to_thread(self._unique_name_sync, source.parent_folder_id, output_name)
        body: dict[str, object] = {
            "name": name,
            "parents": [source.parent_folder_id],
            "appProperties": _initial_properties(operation_id, source_sha256),
        }
        pptx: bytes | None = None
        if source.mime_type == PPTX_MIME_TYPE:
            if not await asyncio.to_thread(self._pptx_import_is_supported_sync):
                raise UnsupportedSourceError
            pptx = await asyncio.to_thread(self._download_pptx_sync, source)
            body["mimeType"] = MIME_SLIDE
        elif source.mime_type != MIME_SLIDE:
            raise UnsupportedSourceError
        if not await self.source_is_unchanged(source):
            raise SourceChangedError
        await self._validate_destination_folder(source.parent_folder_id)
        try:
            raw = await asyncio.to_thread(self._perform_create_sync, source, body, pptx)
        except (DocumentGatewayError, ProviderDeadlineError) as exc:
            recovered = await self.recover_output(
                operation_id,
                expected_parent_folder_id=source.parent_folder_id,
            )
            if recovered is None:
                raise DocumentGatewayError from exc
            if recovered.source_sha256 != source_sha256:
                raise OutputConflictError from exc
            return recovered
        draft = await asyncio.to_thread(
            self._draft_from_file,
            raw,
            source_sha256=source_sha256,
        )
        await self._validate_output_scope(draft, source.parent_folder_id)
        return draft

    @override
    async def get_presentation(self, file_id: FileId) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_presentation_sync, file_id)

    @override
    async def reconcile_pending(self, draft: PresentationDraft) -> PresentationDraft:
        await self._validate_output_scope(draft, draft.parent_folder_id)
        return await asyncio.to_thread(self._reconcile_pending_sync, draft)

    @override
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
    ) -> PresentationDraft:
        if draft.applied_mutation_sha256 == mutation_sha256:
            return draft
        await self._validate_output_scope(draft, draft.parent_folder_id)
        return await asyncio.to_thread(
            self._apply_requests_sync,
            draft=draft,
            mutation_sha256=mutation_sha256,
            requests=requests,
            required_revision_id=required_revision_id,
            expected_semantic_sha256=expected_semantic_sha256,
            expected_protected_structure_sha256=expected_protected_structure_sha256,
            expected_brand_sha256=expected_brand_sha256,
        )

    @override
    async def verify_output(
        self,
        *,
        draft: PresentationDraft,
        title: str,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None = None,
    ) -> PresentationVerification:
        return await asyncio.to_thread(
            self._verify_output_sync,
            draft=draft,
            title=title,
            expected_semantic_sha256=expected_semantic_sha256,
            expected_protected_structure_sha256=expected_protected_structure_sha256,
            expected_brand_sha256=expected_brand_sha256,
        )

    @override
    async def complete(self, *, draft: PresentationDraft) -> None:
        await self._validate_output_scope(draft, draft.parent_folder_id)
        await asyncio.to_thread(
            self._update_properties_sync,
            draft.file_id,
            {_PROP_STATE: _STATE_COMPLETE},
        )

    def _perform_create_sync(
        self,
        source: PresentationSourceSnapshot,
        body: dict[str, object],
        pptx: bytes | None,
    ) -> dict[str, Any]:
        if source.mime_type == MIME_SLIDE:
            if pptx is not None:
                raise DocumentGatewayError
            return self._execute_mutation_dict(
                _request(
                    _member(self._drive().files(), "copy"),
                    fileId=source.file_id,
                    body=body,
                    supportsAllDrives=True,
                    fields=_OUTPUT_FIELDS,
                )
            )
        if source.mime_type != PPTX_MIME_TYPE:
            raise UnsupportedSourceError
        if pptx is None:
            raise DocumentGatewayError
        return self._execute_import_mutation_dict(
            _request(
                _member(self._drive().files(), "create"),
                body=body,
                media_body=MediaIoBaseUpload(
                    BytesIO(pptx),
                    mimetype=PPTX_MIME_TYPE,
                    resumable=False,
                ),
                supportsAllDrives=True,
                fields=_OUTPUT_FIELDS,
            )
        )

    def _source_is_unchanged_sync(self, source: PresentationSourceSnapshot) -> bool:
        metadata = self._files_get_sync(source.file_id)
        capabilities = _required_capabilities(metadata)
        observed = (
            metadata.get("mimeType"),
            metadata.get("name"),
            metadata.get("modifiedTime"),
            metadata.get("version"),
            _optional_parent(metadata),
            _optional_nonnegative_int(metadata, "size"),
            _optional_string(metadata, "md5Checksum"),
            capabilities.get("canDownload") is True,
            capabilities.get("canCopy") is True,
        )
        expected = (
            source.mime_type,
            source.source_name,
            source.modified_time,
            source.drive_version,
            source.parent_folder_id,
            source.size,
            source.md5_checksum,
            source.can_download,
            source.can_copy,
        )
        if observed != expected:
            return False
        if source.revision_id is None:
            return True
        presentation = self._get_presentation_sync(source.file_id)
        return presentation.get("revisionId") == source.revision_id

    def _pptx_import_is_supported_sync(self) -> bool:
        raw = self._execute_dict(
            _request(
                _member(self._drive().about(), "get"),
                fields="importFormats",
            )
        )
        formats = raw.get("importFormats")
        if not isinstance(formats, dict):
            return False
        targets = formats.get(PPTX_MIME_TYPE)
        return isinstance(targets, list) and MIME_SLIDE in targets

    def _download_pptx_sync(self, source: PresentationSourceSnapshot) -> bytes:
        metadata = self._files_get_sync(source.file_id)
        size = _required_nonnegative_int(metadata, "size")
        if size > _MAX_PPTX_BYTES:
            raise SourceTooLargeError
        request = _request(
            _member(self._drive().files(), "get_media"),
            fileId=source.file_id,
            supportsAllDrives=True,
        )
        headers = request.headers
        if headers is None:
            headers = {}
            request.headers = headers
        headers["Range"] = f"bytes=0-{_MAX_PPTX_BYTES}"
        raw = self._execute_bytes(request)
        if len(raw) > _MAX_PPTX_BYTES:
            raise SourceTooLargeError
        if len(raw) != size:
            raise SourceChangedError
        if (
            source.md5_checksum is None
            or hashlib.md5(raw, usedforsecurity=False).hexdigest() != source.md5_checksum
        ):
            raise SourceChangedError
        _validate_pptx_archive(raw)
        return raw

    def _recover_output_sync(self, operation_id: uuid.UUID) -> PresentationDraft | None:
        query = (
            f"appProperties has {{ key='{_PROP_OPERATION}' and value='{operation_id}' }} "
            "and trashed=false"
        )
        raw = self._execute_dict(
            _request(
                _member(self._drive().files(), "list"),
                q=query,
                spaces="drive",
                pageSize=2,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields=f"files({_OUTPUT_FIELDS})",
            )
        )
        files = raw.get("files", [])
        if not isinstance(files, list) or not files:
            return None
        if len(files) != 1 or not isinstance(files[0], dict):
            raise DocumentGatewayError
        properties = files[0].get("appProperties", {})
        source_sha256 = properties.get(_PROP_SOURCE_HASH) if isinstance(properties, dict) else None
        if not isinstance(source_sha256, str):
            raise DocumentGatewayError
        return self._draft_from_file(
            {str(key): value for key, value in files[0].items()},
            source_sha256=source_sha256,
        )

    def _draft_from_file(
        self,
        raw: dict[str, Any],
        *,
        source_sha256: str,
    ) -> PresentationDraft:
        if raw.get("mimeType") != MIME_SLIDE:
            raise DocumentGatewayError
        file_id = FileId(_required_string(raw, "id"))
        properties = raw.get("appProperties", {})
        typed_properties = properties if isinstance(properties, dict) else {}
        presentation = self._get_presentation_sync(file_id)
        return PresentationDraft(
            file_id=file_id,
            name=_required_string(raw, "name"),
            parent_folder_id=_required_parent(raw),
            revision_id=PresentationRevisionId(_required_string(presentation, "revisionId")),
            source_sha256=source_sha256,
            web_url=(raw.get("webViewLink") if isinstance(raw.get("webViewLink"), str) else None),
            applied_mutation_sha256=_optional_string(typed_properties, _PROP_MUTATION),
            pending_mutation_sha256=_optional_string(typed_properties, _PROP_PENDING_MUTATION),
            mutation_base_revision_sha256=_optional_string(
                typed_properties, _PROP_MUTATION_BASE_REVISION_HASH
            ),
            pending_expected_semantic_sha256=_optional_string(
                typed_properties, _PROP_EXPECTED_SEMANTIC_HASH
            ),
            pending_expected_protected_structure_sha256=_optional_string(
                typed_properties, _PROP_EXPECTED_PROTECTED_HASH
            ),
            pending_expected_brand_sha256=_optional_string(
                typed_properties, _PROP_EXPECTED_BRAND_HASH
            ),
            completed=typed_properties.get(_PROP_STATE) == _STATE_COMPLETE,
        )

    def _reconcile_pending_sync(self, draft: PresentationDraft) -> PresentationDraft:
        pending = draft.pending_mutation_sha256
        if pending is None:
            return draft
        expected_semantic = draft.pending_expected_semantic_sha256
        expected_protected = draft.pending_expected_protected_structure_sha256
        expected_brand_hash = draft.pending_expected_brand_sha256
        base_hash = draft.mutation_base_revision_sha256
        if (
            expected_semantic is None
            or expected_protected is None
            or expected_brand_hash is None
            or base_hash is None
        ):
            raise IncompatiblePendingOutputError
        if draft.applied_mutation_sha256 not in {None, pending}:
            raise IncompatiblePendingOutputError
        expected_brand = None if expected_brand_hash == _NO_BRAND_EXPECTED else expected_brand_hash
        presentation = self._get_presentation_sync(draft.file_id)
        verification = self._verify_presentation(
            presentation=presentation,
            title=draft.name,
            expected_semantic_sha256=expected_semantic,
            expected_protected_structure_sha256=expected_protected,
            expected_brand_sha256=expected_brand,
        )
        if _verification_passed(verification):
            if draft.applied_mutation_sha256 is None:
                self._update_properties_sync(draft.file_id, {_PROP_MUTATION: pending})
            return self._read_draft(draft)
        if draft.applied_mutation_sha256 is not None:
            raise IncompatiblePendingOutputError
        revision_id = _required_string(presentation, "revisionId")
        if _revision_sha256(revision_id) == base_hash:
            return self._read_draft(draft)
        raise SourceChangedError

    def _apply_requests_sync(
        self,
        *,
        draft: PresentationDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: PresentationRevisionId,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None,
    ) -> PresentationDraft:
        if draft.revision_id != required_revision_id:
            raise SourceChangedError
        if draft.pending_mutation_sha256 not in {None, mutation_sha256}:
            raise IncompatiblePendingOutputError
        if not requests:
            self._update_properties_sync(draft.file_id, {_PROP_MUTATION: mutation_sha256})
            return self._read_draft(draft)

        base_hash = _revision_sha256(required_revision_id)
        self._update_properties_sync(
            draft.file_id,
            {
                _PROP_PENDING_MUTATION: mutation_sha256,
                _PROP_MUTATION_BASE_REVISION_HASH: base_hash,
                _PROP_EXPECTED_SEMANTIC_HASH: expected_semantic_sha256,
                _PROP_EXPECTED_PROTECTED_HASH: expected_protected_structure_sha256,
                _PROP_EXPECTED_BRAND_HASH: expected_brand_sha256 or _NO_BRAND_EXPECTED,
            },
        )
        try:
            self._execute_mutation_dict(
                _request(
                    _member(self._slides().presentations(), "batchUpdate"),
                    presentationId=draft.file_id,
                    body={
                        "requests": requests,
                        "writeControl": {"requiredRevisionId": required_revision_id},
                    },
                )
            )
        except (DocumentGatewayError, ProviderDeadlineError) as exc:
            presentation = self._get_presentation_sync(draft.file_id)
            verification = self._verify_presentation(
                presentation=presentation,
                title=draft.name,
                expected_semantic_sha256=expected_semantic_sha256,
                expected_protected_structure_sha256=expected_protected_structure_sha256,
                expected_brand_sha256=expected_brand_sha256,
            )
            if _verification_passed(verification):
                self._update_properties_sync(draft.file_id, {_PROP_MUTATION: mutation_sha256})
                return self._read_draft(draft)
            if presentation.get("revisionId") == required_revision_id:
                raise
            raise SourceChangedError from exc

        presentation = self._get_presentation_sync(draft.file_id)
        verification = self._verify_presentation(
            presentation=presentation,
            title=draft.name,
            expected_semantic_sha256=expected_semantic_sha256,
            expected_protected_structure_sha256=expected_protected_structure_sha256,
            expected_brand_sha256=expected_brand_sha256,
        )
        if not _verification_passed(verification):
            raise DocumentGatewayError
        self._update_properties_sync(draft.file_id, {_PROP_MUTATION: mutation_sha256})
        return self._read_draft(draft)

    def _read_draft(self, draft: PresentationDraft) -> PresentationDraft:
        refreshed = self._draft_from_file(
            self._files_get_sync(draft.file_id),
            source_sha256=draft.source_sha256,
        )
        if refreshed.parent_folder_id != draft.parent_folder_id:
            raise UnsupportedSourceError
        return refreshed

    async def _validate_destination_folder(self, folder_id: FileId) -> None:
        folder = await self._get_scoped_file(folder_id)
        if folder.mime_type != MIME_FOLDER or not folder.can_add_children:
            raise UnsupportedSourceError

    async def _validate_output_scope(
        self,
        draft: PresentationDraft,
        expected_parent_folder_id: FileId,
    ) -> None:
        if draft.parent_folder_id != expected_parent_folder_id:
            raise UnsupportedSourceError
        output = await self._get_scoped_file(draft.file_id)
        if (
            output.id != draft.file_id
            or output.mime_type != MIME_SLIDE
            or output.parent_folder_id != expected_parent_folder_id
            or not output.can_edit
        ):
            raise UnsupportedSourceError

    async def _get_scoped_file(self, file_id: FileId) -> FileResponse:
        try:
            return await self._files.get(file_id)
        except FileNotInScopeError as exc:
            raise UnsupportedSourceError from exc
        except FilesError as exc:
            if exc.http_status in {401, 403}:
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc

    def _verify_output_sync(
        self,
        *,
        draft: PresentationDraft,
        title: str,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None,
    ) -> PresentationVerification:
        presentation = self._get_presentation_sync(draft.file_id)
        return self._verify_presentation(
            presentation=presentation,
            title=title,
            expected_semantic_sha256=expected_semantic_sha256,
            expected_protected_structure_sha256=expected_protected_structure_sha256,
            expected_brand_sha256=expected_brand_sha256,
        )

    def _verify_presentation(
        self,
        *,
        presentation: dict[str, Any],
        title: str,
        expected_semantic_sha256: str,
        expected_protected_structure_sha256: str,
        expected_brand_sha256: str | None,
    ) -> PresentationVerification:
        fingerprints = self._fingerprint(presentation, title)
        return PresentationVerification(
            content_preserved=fingerprints.semantic_sha256 == expected_semantic_sha256,
            structure_preserved=(
                fingerprints.protected_structure_sha256 == expected_protected_structure_sha256
            ),
            brand_applied=(
                expected_brand_sha256 is None or fingerprints.brand_sha256 == expected_brand_sha256
            ),
            semantic_sha256=fingerprints.semantic_sha256,
            protected_structure_sha256=fingerprints.protected_structure_sha256,
            brand_sha256=fingerprints.brand_sha256,
        )

    def _get_presentation_sync(self, file_id: FileId) -> dict[str, Any]:
        return self._execute_dict(
            _request(
                _member(self._slides().presentations(), "get"),
                presentationId=file_id,
            )
        )

    def _files_get_sync(self, file_id: FileId) -> dict[str, Any]:
        return self._execute_dict(
            _request(
                _member(self._drive().files(), "get"),
                fileId=file_id,
                supportsAllDrives=True,
                fields=(
                    "id,name,parents,mimeType,modifiedTime,version,size,md5Checksum,"
                    "capabilities(canDownload,canCopy),appProperties,webViewLink"
                ),
            )
        )

    def _unique_name_sync(self, parent_id: FileId, requested: str) -> str:
        escaped = requested.replace("\\", "\\\\").replace("'", "\\'")
        raw = self._execute_dict(
            _request(
                _member(self._drive().files(), "list"),
                q=f"'{parent_id}' in parents and name='{escaped}' and trashed=false",
                pageSize=1,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id)",
            )
        )
        files = raw.get("files", [])
        return requested if not files else f"{requested} ({uuid.uuid7().hex[:8]})"

    def _update_properties_sync(self, file_id: FileId, properties: dict[str, str]) -> None:
        self._execute_mutation_dict(
            _request(
                _member(self._drive().files(), "update"),
                fileId=file_id,
                supportsAllDrives=True,
                body={"appProperties": properties},
                fields="id",
            )
        )

    def _drive(self) -> _DriveProvider:
        if self._drive_provider is not None:
            return self._drive_provider
        thread_id = threading.get_ident()
        client = self._drive_clients.get(thread_id)
        if client is None:
            built = reuse_drive_client(
                self._user,
                {},
                refresh_handler=self._refresh_handler,
            )
            client = built
            self._drive_clients[thread_id] = client
        return client

    def _slides(self) -> _SlidesProvider:
        if self._slides_provider is not None:
            return self._slides_provider
        thread_id = threading.get_ident()
        client = self._slides_clients.get(thread_id)
        if client is None:
            credentials = build_google_credentials(self._user, self._refresh_handler)
            built = _call(
                _member(discovery, "build_from_document"),
                slides_discovery_document(),
                credentials=credentials,
            )
            if not isinstance(built, _SlidesProvider):
                message = "Google Slides discovery builder returned an invalid client"
                raise TypeError(message)
            client = built
            self._slides_clients[thread_id] = client
        return client

    @staticmethod
    def _execute_dict(request: HttpRequest) -> dict[str, Any]:
        try:
            raw: object = execute_with_retry(request)
        except HttpError as exc:
            if _is_terminal_scope_error(exc):
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc
        if not isinstance(raw, dict):
            raise DocumentGatewayError
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _execute_mutation_dict(request: HttpRequest) -> dict[str, Any]:
        try:
            raw: object = execute_mutation(request)
        except HttpError as exc:
            if _is_terminal_scope_error(exc):
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc
        if not isinstance(raw, dict):
            raise DocumentGatewayError
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _execute_import_mutation_dict(request: HttpRequest) -> dict[str, Any]:
        try:
            raw: object = execute_mutation(request)
        except HttpError as exc:
            if (
                _is_terminal_scope_error(exc)
                or getattr(exc.resp, "status", None) == HTTPStatus.BAD_REQUEST
            ):
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc
        if not isinstance(raw, dict):
            raise DocumentGatewayError
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _execute_bytes(request: HttpRequest) -> bytes:
        try:
            raw: object = execute_with_retry(request)
        except HttpError as exc:
            if _is_terminal_scope_error(exc):
                raise UnsupportedSourceError from exc
            raise DocumentGatewayError from exc
        if not isinstance(raw, bytes):
            raise DocumentGatewayError
        return raw


_OUTPUT_FIELDS = "id,name,parents,mimeType,webViewLink,appProperties"


@cache
def slides_discovery_document() -> str:
    content = _call(get_static_doc, "slides", "v1")
    if not isinstance(content, str):
        message = "Bundled Google Slides v1 discovery document not found"
        raise TypeError(message)
    json.loads(content)
    return content


def _default_fingerprint(
    presentation: dict[str, Any],
    title: str,
) -> PresentationFingerprints:
    parsed = parse_google_presentation(presentation, title=title)
    return PresentationFingerprints(
        semantic_sha256=parsed.document.semantic_sha256,
        protected_structure_sha256=parsed.protected_sha256,
    )


def _verification_passed(verification: PresentationVerification) -> bool:
    return (
        verification.content_preserved
        and verification.structure_preserved
        and verification.brand_applied
    )


def _is_terminal_scope_error(exc: HttpError) -> bool:
    status = getattr(exc.resp, "status", None)
    return status in {401, 403} and any(
        marker in exc.content.lower() for marker in _TERMINAL_SCOPE_ERROR_MARKERS
    )


def _initial_properties(operation_id: uuid.UUID, source_sha256: str) -> dict[str, str]:
    return {
        _PROP_OPERATION: str(operation_id),
        _PROP_SOURCE_HASH: source_sha256,
        _PROP_STATE: _STATE_INCOMPLETE,
    }


def _revision_sha256(revision_id: str) -> str:
    return hashlib.sha256(revision_id.encode()).hexdigest()


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise DocumentGatewayError
    return value


def _optional_string(mapping: dict[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) else None


def _required_parent(mapping: dict[str, Any]) -> FileId:
    parent = _optional_parent(mapping)
    if parent is None:
        raise DocumentGatewayError
    return parent


def _optional_parent(mapping: dict[str, Any]) -> FileId | None:
    parents = mapping.get("parents")
    if not isinstance(parents, list) or not parents or not isinstance(parents[0], str):
        return None
    return FileId(parents[0])


def _required_nonnegative_int(mapping: dict[str, Any], key: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.isdecimal():
        raise UnsupportedSourceError
    parsed = int(value)
    if parsed < 0:
        raise UnsupportedSourceError
    return parsed


def _optional_nonnegative_int(mapping: dict[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.isdecimal():
        raise UnsupportedSourceError
    return int(value)


def _required_capabilities(mapping: dict[str, Any]) -> dict[str, Any]:
    capabilities = mapping.get("capabilities")
    if not isinstance(capabilities, dict):
        raise UnsupportedSourceError
    return {str(key): value for key, value in capabilities.items()}


def _validate_scoped_metadata(file: FileResponse, metadata: dict[str, Any]) -> None:
    if (
        metadata.get("id") != file.id
        or metadata.get("name") != file.name
        or metadata.get("mimeType") != file.mime_type
        or metadata.get("modifiedTime") != file.modified_time
        or _optional_parent(metadata) != file.parent_folder_id
    ):
        raise SourceChangedError


def _validate_source_kind(
    *,
    name: str,
    mime_type: str,
    size: int | None,
    md5_checksum: str | None,
    can_download: bool,
    can_copy: bool,
) -> None:
    lower_name = name.casefold()
    has_powerpoint_extension = lower_name.endswith((".ppt", ".pptm", ".pptx"))
    if mime_type == MIME_SLIDE:
        if has_powerpoint_extension or not can_copy:
            raise UnsupportedSourceError
        return
    if (
        mime_type != PPTX_MIME_TYPE
        or not lower_name.endswith(".pptx")
        or size is None
        or md5_checksum is None
        or not can_download
    ):
        raise UnsupportedSourceError


def _validate_pptx_archive(raw: bytes) -> None:
    try:
        with ZipFile(BytesIO(raw)) as archive:
            _validate_zip_entries(archive)
    except (
        BadZipFile,
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zlib.error,
    ) as exc:
        raise UnsupportedSourceError from exc


def _validate_zip_entries(archive: ZipFile) -> None:
    entries = archive.infolist()
    if not entries or len(entries) > _MAX_ZIP_ENTRIES:
        raise UnsupportedSourceError
    names: set[str] = set()
    total_uncompressed = 0
    for entry in entries:
        normalized = entry.filename.replace("\\", "/")
        if _zip_entry_is_unsafe(entry, normalized, names):
            raise UnsupportedSourceError
        total_uncompressed += entry.file_size
        if total_uncompressed > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise SourceTooLargeError
        names.add(normalized)
    if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
        raise UnsupportedSourceError
    content_types = _read_xml_root(
        archive,
        "[Content_Types].xml",
        maximum_bytes=_MAX_CONTENT_TYPES_BYTES,
        expected_root="Types",
    )
    _read_xml_root(
        archive,
        "ppt/presentation.xml",
        maximum_bytes=_MAX_PRESENTATION_XML_BYTES,
        expected_root="presentation",
    )
    if any(
        marker in value.casefold()
        for element in content_types.iter()
        for value in element.attrib.values()
        for marker in _FORBIDDEN_CONTENT_TYPE_MARKERS
    ):
        raise UnsupportedSourceError
    for name in names:
        if name.casefold().endswith(".rels"):
            _validate_relationships(archive, name)


def _validate_relationships(archive: ZipFile, name: str) -> None:
    root = _read_xml_root(
        archive,
        name,
        maximum_bytes=_MAX_RELATIONSHIP_BYTES,
        expected_root="Relationships",
    )
    for relationship in root.iter():
        target_mode = relationship.attrib.get("TargetMode")
        if target_mode is None or target_mode == "Internal":
            continue
        if target_mode != "External":
            raise UnsupportedSourceError
        relationship_type = relationship.attrib.get("Type", "")
        target = relationship.attrib.get("Target", "")
        if (
            relationship_type not in _ALLOWED_EXTERNAL_HYPERLINK_RELATIONSHIP_TYPES
            or len(target) > _MAX_HYPERLINK_TARGET_LENGTH
            or any(ord(character) < _ASCII_CONTROL_LIMIT for character in target)
            or urlsplit(target).scheme.casefold() not in _ALLOWED_EXTERNAL_HYPERLINK_SCHEMES
        ):
            raise UnsupportedSourceError


def _read_xml_root(
    archive: ZipFile,
    name: str,
    *,
    maximum_bytes: int,
    expected_root: str,
) -> _XmlElement:
    entry = archive.getinfo(name)
    if entry.file_size > maximum_bytes:
        raise UnsupportedSourceError
    raw = archive.read(entry)
    if len(raw) > maximum_bytes:
        raise UnsupportedSourceError
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise UnsupportedSourceError
    try:
        root: _XmlElement = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise UnsupportedSourceError from exc
    if str(root.tag).rsplit("}", 1)[-1] != expected_root:
        raise UnsupportedSourceError
    return root


def _zip_entry_is_unsafe(entry: ZipInfo, normalized: str, names: set[str]) -> bool:
    path = PurePosixPath(normalized)
    path_components = {component.casefold() for component in path.parts}
    return any(
        (
            not normalized,
            "\x00" in normalized,
            normalized.startswith("/"),
            ".." in path.parts,
            bool(entry.flag_bits & 0x1),
            entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED},
            entry.file_size > _MAX_ZIP_ENTRY_BYTES,
            stat.S_ISLNK(entry.external_attr >> 16),
            bool(path_components & _FORBIDDEN_PPTX_PATH_COMPONENTS),
            normalized in names,
        )
    )
