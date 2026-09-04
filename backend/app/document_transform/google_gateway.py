from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
import hashlib
from io import BytesIO
import json
import threading
from typing import TYPE_CHECKING, Any, Protocol, override, runtime_checkable
import uuid

from googleapiclient import discovery
from googleapiclient.discovery_cache import get_static_doc
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest, MediaIoBaseUpload

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST, normalize_header_title
from app.document_transform.errors import (
    DocumentGatewayError,
    IncompatiblePendingOutputError,
    UnsupportedSourceError,
)
from app.document_transform.gateway import (
    DocumentGateway,
    OutputDraft,
    OutputVerification,
    SourceSnapshot,
    TabHeader,
)
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock, parse_google_document
from app.document_transform.schemas import DocumentOutput, DriveFileSource, MarkdownSource
from app.drive.retry import execute_mutation, execute_with_retry
from app.drive.schemas import MIME_DOC
from app.drive.service import (
    GoogleRefreshHandler,
    build_google_credentials,
    reuse_drive_client,
)
from app.files.service import FilesService
from app.identifiers import DocsRevisionId, DocsSegmentId, DocsTabId, FileId
from app.models.user import User


_RGB_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class ParagraphRangeKey:
    tab_id: str
    start_index: int
    end_index: int


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource


_MAX_MARKDOWN_BYTES = 2 * 1024 * 1024
_PROP_OPERATION = "sarjTransformOperation"
_PROP_SOURCE_HASH = "sarjTransformSourceHash"
_PROP_STATE = "sarjTransformState"
_PROP_MUTATION = "sarjTransformMutation"
_PROP_PENDING_MUTATION = "sarjTransformPendingMutation"
_PROP_MUTATION_BASE_REVISION_HASH = "sarjTransformMutationBaseHash"
_STATE_INCOMPLETE = "incomplete"
_STATE_COMPLETE = "complete"


@runtime_checkable
class _DocumentsProvider(Protocol):
    def documents(self) -> object: ...


@runtime_checkable
class _BoundaryCallable(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


def _member(owner: object, name: str) -> object:
    member = getattr(owner, name, None)
    if member is None:
        msg = f"{type(owner).__name__} has no {name} member"
        raise TypeError(msg)
    return member


def _call_boundary(candidate: object, *args: object, **kwargs: object) -> object:
    if not isinstance(candidate, _BoundaryCallable):
        msg = "External API boundary is not callable"
        raise TypeError(msg)
    return candidate(*args, **kwargs)


def _google_http_request(candidate: object, **kwargs: object) -> HttpRequest:
    request = _call_boundary(candidate, **kwargs)
    if not isinstance(request, HttpRequest):
        msg = "Google API method did not return an HTTP request"
        raise TypeError(msg)
    return request


class GoogleDocumentGateway(DocumentGateway):
    def __init__(
        self,
        *,
        user: User,
        files: FilesService,
        logo_uri: str,
        refresh_handler: GoogleRefreshHandler | None = None,
    ) -> None:
        self._user = user
        self._files = files
        self._logo_uri = logo_uri
        self._refresh_handler = refresh_handler
        self._drive_clients: dict[int, DriveResource] = {}
        self._docs_clients: dict[int, _DocumentsProvider] = {}

    @override
    async def load_drive_source(self, source: DriveFileSource) -> SourceSnapshot:
        file = await self._files.get(source.file_id)
        if file.parent_folder_id is None:
            raise UnsupportedSourceError
        if file.mime_type == MIME_DOC:
            document = await asyncio.to_thread(self._get_document_sync, source.file_id)
            revision_id = document.get("revisionId")
            if not isinstance(revision_id, str):
                raise DocumentGatewayError
            return SourceSnapshot(
                title=file.name,
                parent_folder_id=file.parent_folder_id,
                modified_time=file.modified_time,
                revision_id=DocsRevisionId(revision_id),
                media_type=file.mime_type,
                google_document=document,
            )
        if file.mime_type != "text/markdown":
            raise UnsupportedSourceError
        markdown = await asyncio.to_thread(self._download_markdown_sync, source.file_id)
        return SourceSnapshot(
            title=file.name.removesuffix(".md"),
            parent_folder_id=file.parent_folder_id,
            modified_time=file.modified_time,
            revision_id=None,
            media_type=file.mime_type,
            markdown=markdown,
        )

    @override
    async def recover_output(self, operation_id: uuid.UUID) -> OutputDraft | None:
        return await asyncio.to_thread(self._recover_output_sync, operation_id)

    @override
    async def source_is_unchanged(
        self,
        source: DriveFileSource,
        *,
        expected_modified_time: str,
        expected_revision_id: DocsRevisionId | None,
    ) -> bool:
        file = await self._files.get(source.file_id)
        if file.modified_time != expected_modified_time:
            return False
        if expected_revision_id is None:
            return True
        document = await asyncio.to_thread(self._get_document_sync, source.file_id)
        return document.get("revisionId") == expected_revision_id

    @override
    async def create_from_drive_copy(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
    ) -> OutputDraft:
        return await asyncio.to_thread(
            self._copy_sync,
            operation_id,
            source,
            source_sha256,
            output_name,
        )

    @override
    async def create_from_drive_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
        imported_docx: bytes | None = None,
    ) -> OutputDraft:
        del source
        return await asyncio.to_thread(
            self._create_sync,
            operation_id,
            source_sha256,
            output_name,
            parent_folder_id,
            imported_docx,
        )

    @override
    async def create_from_markdown(
        self,
        *,
        operation_id: uuid.UUID,
        source: MarkdownSource,
        source_sha256: str,
        output_name: str,
        imported_docx: bytes | None = None,
    ) -> OutputDraft:
        return await asyncio.to_thread(
            self._create_sync,
            operation_id,
            source_sha256,
            output_name,
            source.destination_folder_id,
            imported_docx,
        )

    @override
    async def prepare_brand_shell(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
    ) -> OutputDraft:
        return await asyncio.to_thread(self._prepare_brand_shell_sync, operation_id, draft)

    @override
    async def apply_requests_once(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: DocsRevisionId,
    ) -> OutputDraft:
        del operation_id
        if draft.applied_mutation_sha256 == mutation_sha256:
            return draft
        return await asyncio.to_thread(
            self._apply_requests_sync,
            draft,
            mutation_sha256,
            requests,
            required_revision_id,
        )

    @override
    async def verify_output(
        self,
        *,
        draft: OutputDraft,
        source: SourceSnapshot,
        expected: DocumentIR,
        verify_brand: bool,
    ) -> OutputVerification:
        return await asyncio.to_thread(
            self._verify_output_sync,
            draft,
            source,
            expected,
            verify_brand=verify_brand,
        )

    @override
    async def complete(
        self,
        *,
        operation_id: uuid.UUID,
        draft: OutputDraft,
        result: dict[str, Any],
    ) -> None:
        del operation_id, result
        await asyncio.to_thread(
            self._update_properties_sync,
            draft.document_id,
            {_PROP_STATE: _STATE_COMPLETE},
        )

    def _copy_sync(
        self,
        operation_id: uuid.UUID,
        source: DriveFileSource,
        source_sha256: str,
        output_name: str,
    ) -> OutputDraft:
        file = self._files_get_sync(source.file_id)
        parent_id = _required_parent(file)
        name = self._unique_name_sync(parent_id, output_name)
        body = {
            "name": name,
            "parents": [parent_id],
            "appProperties": _initial_properties(operation_id, source_sha256),
        }
        try:
            raw = self._execute_mutation_dict(
                _google_http_request(
                    _member(self._drive().files(), "copy"),
                    fileId=source.file_id,
                    body=body,
                    supportsAllDrives=True,
                    fields="id,name,parents,webViewLink,appProperties",
                )
            )
        except DocumentGatewayError:
            return self._recover_ambiguous_create(operation_id, source_sha256)
        return self._draft_from_file(raw, source_sha256=source_sha256)

    def _create_sync(
        self,
        operation_id: uuid.UUID,
        source_sha256: str,
        output_name: str,
        parent_folder_id: FileId,
        imported_docx: bytes | None,
    ) -> OutputDraft:
        name = self._unique_name_sync(parent_folder_id, output_name)
        body = {
            "name": name,
            "mimeType": MIME_DOC,
            "parents": [parent_folder_id],
            "appProperties": _initial_properties(operation_id, source_sha256),
        }
        media = (
            MediaIoBaseUpload(
                BytesIO(imported_docx),
                mimetype=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                resumable=False,
            )
            if imported_docx is not None
            else None
        )
        try:
            raw = self._execute_mutation_dict(
                _google_http_request(
                    _member(self._drive().files(), "create"),
                    body=body,
                    media_body=media,
                    supportsAllDrives=True,
                    fields="id,name,parents,webViewLink,appProperties",
                )
            )
        except DocumentGatewayError:
            return self._recover_ambiguous_create(operation_id, source_sha256)
        return self._draft_from_file(raw, source_sha256=source_sha256)

    def _recover_ambiguous_create(
        self,
        operation_id: uuid.UUID,
        source_sha256: str,
    ) -> OutputDraft:
        recovered = self._recover_output_sync(operation_id)
        if recovered is None:
            raise DocumentGatewayError
        if recovered.source_sha256 != source_sha256:
            raise DocumentGatewayError
        return recovered

    def _recover_output_sync(self, operation_id: uuid.UUID) -> OutputDraft | None:
        query = (
            f"appProperties has {{ key='{_PROP_OPERATION}' and value='{operation_id}' }} "
            "and trashed=false"
        )
        raw = self._execute_dict(
            self._drive()
            .files()
            .list(
                q=query,
                spaces="drive",
                pageSize=2,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id,name,parents,webViewLink,appProperties)",
            )
        )
        files = raw.get("files", [])
        if not isinstance(files, list) or not files:
            return None
        if len(files) != 1 or not isinstance(files[0], dict):
            raise DocumentGatewayError
        properties = files[0].get("appProperties", {})
        if not isinstance(properties, dict):
            raise DocumentGatewayError
        source_sha256 = properties.get(_PROP_SOURCE_HASH)
        if not isinstance(source_sha256, str):
            raise DocumentGatewayError
        return self._draft_from_file(files[0], source_sha256=source_sha256)

    def _draft_from_file(self, raw: dict[str, Any], *, source_sha256: str) -> OutputDraft:
        file_id = _required_string(raw, "id")
        name = _required_string(raw, "name")
        parents = raw.get("parents")
        if not isinstance(parents, list) or not parents or not isinstance(parents[0], str):
            raise DocumentGatewayError
        document = self._get_document_sync(FileId(file_id))
        revision_id = _required_string(document, "revisionId")
        tab_id = _first_tab_id(document)
        properties = raw.get("appProperties", {})
        properties = properties if isinstance(properties, dict) else {}
        return OutputDraft(
            output=DocumentOutput(
                file_id=FileId(file_id),
                name=name,
                parent_folder_id=FileId(parents[0]),
                web_url=raw.get("webViewLink") if isinstance(raw.get("webViewLink"), str) else None,
            ),
            document_id=FileId(file_id),
            tab_id=tab_id,
            revision_id=DocsRevisionId(revision_id),
            source_sha256=source_sha256,
            headers=(),
            logo_uri=self._logo_uri,
            applied_mutation_sha256=_optional_property(properties, _PROP_MUTATION),
            pending_mutation_sha256=_optional_property(properties, _PROP_PENDING_MUTATION),
            mutation_base_revision_sha256=_optional_property(
                properties, _PROP_MUTATION_BASE_REVISION_HASH
            ),
            completed=properties.get(_PROP_STATE) == _STATE_COMPLETE,
            brand_warnings=(),
        )

    def _prepare_brand_shell_sync(
        self,
        operation_id: uuid.UUID,
        draft: OutputDraft,
    ) -> OutputDraft:
        del operation_id
        if draft.headers:
            return draft
        document = self._get_document_sync(draft.document_id)
        tabs = tuple(_document_tabs(document))
        requests: list[dict[str, object]] = []
        warnings: list[str] = [] if self._logo_uri else ["logo_unavailable"]
        for tab_id, document_tab in tabs:
            style = document_tab.get("documentStyle", {})
            header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
            headers = document_tab.get("headers", {})
            if (
                draft.applied_mutation_sha256 is None
                and isinstance(header_id, str)
                and _segment_has_text(headers, header_id)
            ):
                warnings.append(f"header_conflict:{tab_id}")
                continue
            if not isinstance(header_id, str):
                requests.append(
                    {
                        "createHeader": {
                            "type": "DEFAULT",
                            "sectionBreakLocation": {"index": 0, "tabId": tab_id},
                        }
                    }
                )
        if requests:
            self._execute_mutation_dict(
                _google_http_request(
                    _member(self._docs().documents(), "batchUpdate"),
                    documentId=draft.document_id,
                    body={
                        "requests": requests,
                        "writeControl": {"requiredRevisionId": draft.revision_id},
                    },
                )
            )
            document = self._get_document_sync(draft.document_id)
            tabs = tuple(_document_tabs(document))
        tab_headers: list[TabHeader] = []
        for tab_id, document_tab in tabs:
            style = document_tab.get("documentStyle", {})
            header_id = style.get("defaultHeaderId") if isinstance(style, dict) else None
            if not isinstance(header_id, str):
                if f"header_conflict:{tab_id}" in warnings:
                    continue
                raise DocumentGatewayError
            headers = document_tab.get("headers", {})
            if draft.applied_mutation_sha256 is None and _segment_has_text(headers, header_id):
                if f"header_conflict:{tab_id}" not in warnings:
                    warnings.append(f"header_conflict:{tab_id}")
                continue
            tab_headers.append(
                TabHeader(tab_id=DocsTabId(tab_id), header_id=DocsSegmentId(header_id))
            )
        revision_id = _required_string(document, "revisionId")
        return OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=DocsRevisionId(revision_id),
            source_sha256=draft.source_sha256,
            headers=tuple(tab_headers),
            logo_uri=self._logo_uri,
            applied_mutation_sha256=draft.applied_mutation_sha256,
            pending_mutation_sha256=draft.pending_mutation_sha256,
            mutation_base_revision_sha256=draft.mutation_base_revision_sha256,
            completed=draft.completed,
            result=draft.result,
            brand_warnings=tuple(warnings),
        )

    def _apply_requests_sync(
        self,
        draft: OutputDraft,
        mutation_sha256: str,
        requests: list[dict[str, Any]],
        required_revision_id: DocsRevisionId,
    ) -> OutputDraft:
        if draft.pending_mutation_sha256 not in {None, mutation_sha256}:
            raise IncompatiblePendingOutputError
        if (
            draft.pending_mutation_sha256 == mutation_sha256
            and draft.mutation_base_revision_sha256 is not None
            and _revision_sha256(draft.revision_id) != draft.mutation_base_revision_sha256
        ):
            self._update_properties_sync(
                draft.document_id,
                {_PROP_MUTATION: mutation_sha256},
            )
            return self._draft_with_mutation(draft, mutation_sha256)
        if requests:
            self._update_properties_sync(
                draft.document_id,
                {
                    _PROP_PENDING_MUTATION: mutation_sha256,
                    _PROP_MUTATION_BASE_REVISION_HASH: _revision_sha256(required_revision_id),
                },
            )
            self._execute_mutation_dict(
                _google_http_request(
                    _member(self._docs().documents(), "batchUpdate"),
                    documentId=draft.document_id,
                    body={
                        "requests": requests,
                        "writeControl": {"requiredRevisionId": required_revision_id},
                    },
                )
            )
        self._update_properties_sync(
            draft.document_id,
            {_PROP_MUTATION: mutation_sha256},
        )
        document = self._get_document_sync(draft.document_id)
        return OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=DocsRevisionId(_required_string(document, "revisionId")),
            source_sha256=draft.source_sha256,
            headers=draft.headers,
            logo_uri=draft.logo_uri,
            applied_mutation_sha256=mutation_sha256,
            pending_mutation_sha256=mutation_sha256 if requests else None,
            mutation_base_revision_sha256=(
                _revision_sha256(required_revision_id) if requests else None
            ),
            brand_warnings=draft.brand_warnings,
        )

    def _verify_output_sync(
        self,
        draft: OutputDraft,
        source: SourceSnapshot,
        expected: DocumentIR,
        *,
        verify_brand: bool,
    ) -> OutputVerification:
        output_document = self._get_document_sync(draft.document_id)
        parsed = parse_google_document(output_document, title=expected.title)
        source_document = source.google_document
        structure_preserved = source_document is None or (
            _structural_inventory(source_document) == _structural_inventory(output_document)
            and _protected_structure_inventory(source_document)
            == _protected_structure_inventory(output_document)
        )
        return OutputVerification(
            content_preserved=parsed.semantic_sha256 == expected.semantic_sha256,
            structure_preserved=structure_preserved,
            brand_applied=not verify_brand or verify_brand_application(output_document, parsed),
        )

    @staticmethod
    def _draft_with_mutation(draft: OutputDraft, mutation_sha256: str) -> OutputDraft:
        return OutputDraft(
            output=draft.output,
            document_id=draft.document_id,
            tab_id=draft.tab_id,
            revision_id=draft.revision_id,
            source_sha256=draft.source_sha256,
            headers=draft.headers,
            logo_uri=draft.logo_uri,
            applied_mutation_sha256=mutation_sha256,
            pending_mutation_sha256=draft.pending_mutation_sha256,
            mutation_base_revision_sha256=draft.mutation_base_revision_sha256,
            brand_warnings=draft.brand_warnings,
            completed=draft.completed,
            result=draft.result,
        )

    def _download_markdown_sync(self, file_id: FileId) -> str:
        raw = self._execute_bytes(
            self._drive().files().get_media(fileId=file_id, supportsAllDrives=True)
        )
        if len(raw) > _MAX_MARKDOWN_BYTES:
            raise UnsupportedSourceError
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedSourceError from exc

    def _get_document_sync(self, file_id: FileId) -> dict[str, Any]:
        return self._execute_dict(
            _google_http_request(
                _member(self._docs().documents(), "get"),
                documentId=file_id,
                includeTabsContent=True,
                suggestionsViewMode="SUGGESTIONS_INLINE",
            )
        )

    def _files_get_sync(self, file_id: FileId) -> dict[str, Any]:
        return self._execute_dict(
            self._drive()
            .files()
            .get(
                fileId=file_id,
                supportsAllDrives=True,
                fields="id,name,parents,mimeType,modifiedTime,appProperties",
            )
        )

    def _unique_name_sync(self, parent_id: FileId, requested: str) -> str:
        escaped = requested.replace("\\", "\\\\").replace("'", "\\'")
        raw = self._execute_dict(
            self._drive()
            .files()
            .list(
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
            self._drive()
            .files()
            .update(
                fileId=file_id,
                supportsAllDrives=True,
                body={"appProperties": properties},
                fields="id",
            )
        )

    def _drive(self) -> DriveResource:
        return reuse_drive_client(
            self._user,
            self._drive_clients,
            refresh_handler=self._refresh_handler,
        )

    def _docs(self) -> _DocumentsProvider:
        thread_id = threading.get_ident()
        client = self._docs_clients.get(thread_id)
        if client is None:
            credentials = build_google_credentials(self._user, self._refresh_handler)
            built = _call_boundary(
                _member(discovery, "build_from_document"),
                docs_discovery_document(),
                credentials=credentials,
            )
            if not isinstance(built, _DocumentsProvider):
                msg = "Google Docs discovery builder returned an invalid client"
                raise TypeError(msg)
            client = built
            self._docs_clients[thread_id] = client
        return client

    @staticmethod
    def _execute_dict(request: HttpRequest) -> dict[str, Any]:
        try:
            raw: object = execute_with_retry(request)
        except HttpError as exc:
            raise DocumentGatewayError from exc
        if isinstance(raw, dict):
            return {str(key): value for key, value in raw.items()}
        raise DocumentGatewayError

    @staticmethod
    def _execute_mutation_dict(request: HttpRequest) -> dict[str, Any]:
        try:
            raw: object = execute_mutation(request)
        except HttpError as exc:
            raise DocumentGatewayError from exc
        if isinstance(raw, dict):
            return {str(key): value for key, value in raw.items()}
        raise DocumentGatewayError

    @staticmethod
    def _execute_bytes(request: HttpRequest) -> bytes:
        try:
            raw: object = execute_with_retry(request)
        except HttpError as exc:
            raise DocumentGatewayError from exc
        if isinstance(raw, bytes):
            return raw
        raise DocumentGatewayError


@cache
def docs_discovery_document() -> str:
    provider: object = get_static_doc
    content = _call_boundary(provider, "docs", "v1")
    if not isinstance(content, str):
        msg = "Bundled Google Docs v1 discovery document not found"
        raise TypeError(msg)
    json.loads(content)
    return content


def _initial_properties(operation_id: uuid.UUID, source_sha256: str) -> dict[str, str]:
    return {
        _PROP_OPERATION: str(operation_id),
        _PROP_SOURCE_HASH: source_sha256,
        _PROP_STATE: _STATE_INCOMPLETE,
    }


def _required_parent(file: dict[str, Any]) -> FileId:
    parents = file.get("parents")
    if not isinstance(parents, list) or not parents or not isinstance(parents[0], str):
        raise UnsupportedSourceError
    return FileId(parents[0])


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise DocumentGatewayError
    return value


def _optional_property(properties: dict[str, Any], key: str) -> str | None:
    value = properties.get(key)
    return value if isinstance(value, str) else None


def _revision_sha256(revision_id: DocsRevisionId) -> str:
    return hashlib.sha256(revision_id.encode()).hexdigest()


def _first_tab_id(document: dict[str, Any]) -> DocsTabId:
    tabs = document.get("tabs")
    if not isinstance(tabs, list) or not tabs or not isinstance(tabs[0], dict):
        raise DocumentGatewayError
    properties = tabs[0].get("tabProperties")
    if not isinstance(properties, dict):
        raise DocumentGatewayError
    return DocsTabId(_required_string(properties, "tabId"))


def _segment_has_text(segments: object, segment_id: str) -> bool:
    if not isinstance(segments, dict):
        return False
    segment = segments.get(segment_id, {})
    if not isinstance(segment, dict):
        return False
    content = segment.get("content", [])
    if not isinstance(content, list):
        return False
    return any(
        _structural_text({str(key): value for key, value in item.items()}).strip()
        for item in content
        if isinstance(item, dict)
    )


def _document_tabs(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    tabs = document.get("tabs")
    if not isinstance(tabs, list) or not tabs:
        raise DocumentGatewayError
    yield from _walk_document_tabs(tabs)


def _walk_document_tabs(tabs: list[Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for tab in tabs:
        if not isinstance(tab, dict):
            raise DocumentGatewayError
        properties = tab.get("tabProperties")
        if not isinstance(properties, dict):
            raise DocumentGatewayError
        tab_id = _required_string(properties, "tabId")
        document_tab = tab.get("documentTab")
        if not isinstance(document_tab, dict):
            raise DocumentGatewayError
        yield tab_id, document_tab
        children = tab.get("childTabs", [])
        if not isinstance(children, list):
            raise DocumentGatewayError
        yield from _walk_document_tabs(children)


def _structural_text(element: dict[str, Any]) -> str:
    paragraph = element.get("paragraph", {})
    elements = paragraph.get("elements", []) if isinstance(paragraph, dict) else []
    if not isinstance(elements, list):
        return ""
    return "".join(
        content
        for item in elements
        if isinstance(item, dict)
        and isinstance(item.get("textRun"), dict)
        and isinstance((content := item["textRun"].get("content")), str)
    )


def _structural_inventory(document: dict[str, Any]) -> tuple[tuple[int, ...], ...]:
    inventory: list[tuple[int, ...]] = []
    for _, document_tab in _document_tabs(document):
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            raise DocumentGatewayError
        counts = {
            "paragraph": 0,
            "sectionBreak": 0,
            "table": 0,
            "tableOfContents": 0,
            "inlineObjectElement": 0,
            "footnoteReference": 0,
            "horizontalRule": 0,
        }
        for element in content:
            if not isinstance(element, dict):
                raise DocumentGatewayError
            _count_structural_keys(element, counts)
        inventory.append(tuple(counts[key] for key in sorted(counts)))
    return tuple(inventory)


def _count_structural_keys(value: object, counts: dict[str, int]) -> None:
    match value:
        case dict() as mapping:
            for key, child in mapping.items():
                if isinstance(key, str) and key in counts:
                    counts[key] += 1
                _count_structural_keys(child, counts)
        case list() as items:
            for child in items:
                _count_structural_keys(child, counts)
        case _:
            pass


def _protected_structure_inventory(document: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    inventory: list[tuple[str, str]] = []
    for tab_id, document_tab in _document_tabs(document):
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            raise DocumentGatewayError
        protected = [
            _strip_location_fields(element)
            for element in content
            if isinstance(element, dict) and not _is_plain_text_paragraph(element)
        ]
        inventory.append(
            (
                tab_id,
                json.dumps(protected, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            )
        )
    return tuple(inventory)


def _is_plain_text_paragraph(element: dict[str, Any]) -> bool:
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    parts = paragraph.get("elements", [])
    return isinstance(parts, list) and all(
        isinstance(part, dict) and isinstance(part.get("textRun"), dict) for part in parts
    )


def _strip_location_fields(value: object) -> object:
    match value:
        case dict() as mapping:
            return {
                key: _strip_location_fields(child)
                for key, child in mapping.items()
                if key not in {"startIndex", "endIndex"}
            }
        case list() as items:
            return [_strip_location_fields(child) for child in items]
        case _:
            return value


def verify_brand_application(document: dict[str, Any], parsed: DocumentIR) -> bool:
    tabs = tuple(_document_tabs(document))
    if not tabs or not all(
        _tab_shell_is_branded(
            document,
            document_tab,
            expected_title=parsed.title,
            allow_legacy_inline_objects=len(tabs) == 1,
        )
        for _, document_tab in tabs
    ):
        return False
    paragraphs = _paragraphs_by_range(tabs)
    for block in parsed.blocks:
        source_range = block.source_range
        if source_range is None or not block.editable or block.kind is BlockKind.BLANK:
            continue
        paragraph = paragraphs.get(
            ParagraphRangeKey(
                tab_id=source_range.tab_id,
                start_index=source_range.start_index,
                end_index=source_range.end_index,
            )
        )
        if paragraph is None or not _paragraph_has_brand(paragraph, block):
            return False
    return True


def _tab_shell_is_branded(
    document: dict[str, Any],
    document_tab: dict[str, Any],
    *,
    expected_title: str,
    allow_legacy_inline_objects: bool,
) -> bool:
    style = document_tab.get("documentStyle")
    if not isinstance(style, dict):
        return False
    expected_margin = CURRENT_BRAND_MANIFEST.document.horizontal_margin_pt
    expected_vertical = CURRENT_BRAND_MANIFEST.document.vertical_margin_pt
    if not (
        _dimension_matches(style.get("marginLeft"), expected_margin)
        and _dimension_matches(style.get("marginRight"), expected_margin)
        and _dimension_matches(style.get("marginTop"), expected_vertical)
        and _dimension_matches(style.get("marginBottom"), expected_vertical)
    ):
        return False
    header_id = style.get("defaultHeaderId")
    headers = document_tab.get("headers")
    if not isinstance(header_id, str) or not isinstance(headers, dict):
        return False
    header = headers.get(header_id)
    if not isinstance(header, dict) or not _segment_has_text(headers, header_id):
        return False
    if not _header_has_brand(header, expected_title=expected_title):
        return False
    object_ids = _values_for_key(header, "inlineObjectId")
    if len(object_ids) != 1:
        return False
    inline_objects = document_tab.get("inlineObjects")
    if not isinstance(inline_objects, dict) and allow_legacy_inline_objects:
        inline_objects = document.get("inlineObjects")
    return isinstance(inline_objects, dict) and _inline_object_uses_canonical_logo(
        inline_objects.get(object_ids[0])
    )


def _header_has_brand(header: dict[str, Any], *, expected_title: str) -> bool:
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    if header_tokens is None:
        return False
    content = header.get("content")
    if not isinstance(content, list):
        return False
    if len(content) != 1 or not isinstance(content[0], dict):
        return False
    structural_element = content[0]
    if set(structural_element) - {"startIndex", "endIndex", "paragraph"}:
        return False
    paragraph = structural_element.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    paragraph_style = paragraph.get("paragraphStyle")
    elements = paragraph.get("elements")
    if not isinstance(paragraph_style, dict) or not isinstance(elements, list):
        return False
    logo_positions = [
        index
        for index, element in enumerate(elements)
        if isinstance(element, dict) and isinstance(element.get("inlineObjectElement"), dict)
    ]
    if len(logo_positions) != 1:
        return False
    logo_position = logo_positions[0]
    title_elements = elements[:logo_position]
    trailing_elements = elements[logo_position + 1 :]
    if not title_elements or not all(
        isinstance(element, dict) and isinstance(element.get("textRun"), dict)
        for element in title_elements
    ):
        return False
    if not all(
        isinstance(element, dict)
        and isinstance(element.get("textRun"), dict)
        and isinstance(element["textRun"].get("content"), str)
        and not element["textRun"]["content"].strip()
        for element in trailing_elements
    ):
        return False
    if not all(
        (
            paragraph_style.get("namedStyleType") == "NORMAL_TEXT",
            paragraph_style.get("alignment") == header_tokens.alignment,
            paragraph_style.get("lineSpacing") == header_tokens.line_spacing_percent,
            _dimension_matches(paragraph_style.get("spaceAbove"), header_tokens.space_above_pt),
            _dimension_matches(paragraph_style.get("spaceBelow"), header_tokens.space_below_pt),
            paragraph_style.get("keepWithNext") is False,
        )
    ):
        return False
    text_runs = [element["textRun"] for element in title_elements]
    if not all(isinstance(run.get("content"), str) for run in text_runs):
        return False
    normalized_title = normalize_header_title(
        expected_title,
        max_chars=header_tokens.max_title_chars,
    )
    if "".join(run["content"] for run in text_runs) != f"{normalized_title}\t":
        return False
    return bool(text_runs) and all(_header_text_run_has_brand(run) for run in text_runs)


def _header_text_run_has_brand(text_run: dict[str, Any]) -> bool:
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    if header_tokens is None:
        return False
    style = text_run.get("textStyle")
    if not isinstance(style, dict):
        return False
    family = style.get("weightedFontFamily")
    return (
        isinstance(family, dict)
        and family.get("fontFamily") == header_tokens.font_family
        and _dimension_matches(style.get("fontSize"), header_tokens.size_pt)
        and _color_matches(style.get("foregroundColor"), header_tokens.color)
        and style.get("bold") is header_tokens.bold
    )


def _paragraphs_by_range(
    tabs: tuple[tuple[str, dict[str, Any]], ...],
) -> dict[ParagraphRangeKey, dict[str, Any]]:
    paragraphs: dict[ParagraphRangeKey, dict[str, Any]] = {}
    for tab_id, document_tab in tabs:
        body = document_tab.get("body", {})
        content = body.get("content", []) if isinstance(body, dict) else []
        if not isinstance(content, list):
            return {}
        for element in content:
            if not isinstance(element, dict) or not isinstance(element.get("paragraph"), dict):
                continue
            start = element.get("startIndex")
            end = element.get("endIndex")
            if isinstance(start, int) and isinstance(end, int):
                paragraphs[
                    ParagraphRangeKey(
                        tab_id=tab_id,
                        start_index=start,
                        end_index=end - 1,
                    )
                ] = element["paragraph"]
    return paragraphs


def _paragraph_has_brand(paragraph: dict[str, Any], block: TextBlock) -> bool:
    elements = paragraph.get("elements", [])
    paragraph_style = paragraph.get("paragraphStyle", {})
    if not isinstance(elements, list) or not isinstance(paragraph_style, dict):
        return False
    expected_named_style = (
        f"HEADING_{block.level}"
        if block.kind is BlockKind.HEADING and block.level in {1, 2, 3, 4, 5, 6}
        else "NORMAL_TEXT"
    )
    style_checks = (
        paragraph_style.get("namedStyleType") == expected_named_style,
        paragraph_style.get("alignment") == "START",
        paragraph_style.get("lineSpacing") == CURRENT_BRAND_MANIFEST.document.line_spacing_percent,
        _dimension_matches(
            paragraph_style.get("spaceAbove"),
            CURRENT_BRAND_MANIFEST.document.paragraph_space_above_pt,
        ),
        _dimension_matches(
            paragraph_style.get("spaceBelow"),
            CURRENT_BRAND_MANIFEST.document.paragraph_space_below_pt,
        ),
        paragraph_style.get("keepWithNext") is (block.kind is BlockKind.HEADING),
    )
    if not all(style_checks):
        return False
    expected_size = _brand_font_size(block)
    expected_color = (
        CURRENT_BRAND_MANIFEST.colors.primary
        if block.kind is BlockKind.HEADING
        else CURRENT_BRAND_MANIFEST.colors.ink
    )
    return all(
        _text_run_has_brand(part["textRun"], expected_size, expected_color, block)
        for part in elements
        if isinstance(part, dict)
        and isinstance(part.get("textRun"), dict)
        and isinstance(part["textRun"].get("content"), str)
        and part["textRun"]["content"].strip()
    )


def _text_run_has_brand(
    text_run: dict[str, Any],
    expected_size: int,
    expected_color: str,
    block: TextBlock,
) -> bool:
    style = text_run.get("textStyle")
    if not isinstance(style, dict):
        return False
    family = style.get("weightedFontFamily")
    color = style.get("foregroundColor")
    return (
        isinstance(family, dict)
        and family.get("fontFamily") == CURRENT_BRAND_MANIFEST.document.font_family
        and _dimension_matches(style.get("fontSize"), expected_size)
        and _color_matches(color, expected_color)
        and (block.kind is not BlockKind.HEADING or style.get("bold") is True)
    )


def _brand_font_size(block: TextBlock) -> int:
    document = CURRENT_BRAND_MANIFEST.document
    if block.level is None:
        return document.body_size_pt
    return {
        1: document.h1_size_pt,
        2: document.h2_size_pt,
        3: document.h3_size_pt,
    }.get(block.level, document.body_size_pt)


def _dimension_matches(value: object, magnitude: int) -> bool:
    return (
        isinstance(value, dict)
        and value.get("unit") == "PT"
        and value.get("magnitude") == magnitude
    )


def _color_matches(value: object, expected_hex: str) -> bool:
    if not isinstance(value, dict):
        return False
    color = value.get("color")
    rgb = color.get("rgbColor") if isinstance(color, dict) else None
    if not isinstance(rgb, dict):
        return False
    expected = tuple(int(expected_hex[index : index + 2], 16) / 255 for index in (1, 3, 5))
    observed = tuple(rgb.get(channel) for channel in ("red", "green", "blue"))
    return all(
        isinstance(actual, int | float) and abs(actual - target) < _RGB_TOLERANCE
        for actual, target in zip(observed, expected, strict=True)
    )


def _values_for_key(value: object, target: str) -> tuple[str, ...]:
    values: list[str] = []
    match value:
        case dict() as mapping:
            candidate = mapping.get(target)
            if isinstance(candidate, str):
                values.append(candidate)
            for child in mapping.values():
                values.extend(_values_for_key(child, target))
        case list() as items:
            for child in items:
                values.extend(_values_for_key(child, target))
        case _:
            pass
    return tuple(values)


def _inline_object_uses_canonical_logo(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    properties = value.get("inlineObjectProperties")
    embedded = properties.get("embeddedObject") if isinstance(properties, dict) else None
    image = embedded.get("imageProperties") if isinstance(embedded, dict) else None
    size = embedded.get("size") if isinstance(embedded, dict) else None
    header_tokens = CURRENT_BRAND_MANIFEST.document.header
    asset_width = CURRENT_BRAND_MANIFEST.assets.logo_pixel_width
    asset_height = CURRENT_BRAND_MANIFEST.assets.logo_pixel_height
    return (
        header_tokens is not None
        and asset_width is not None
        and asset_height is not None
        and isinstance(image, dict)
        and image.get("sourceUri") == CURRENT_BRAND_MANIFEST.assets.logo_uri
        and isinstance(size, dict)
        and _fitted_logo_size_matches(
            size,
            pixel_width=asset_width,
            pixel_height=asset_height,
            box_width=header_tokens.logo_width_pt,
            box_height=header_tokens.logo_height_pt,
            tolerance=header_tokens.logo_size_tolerance_pt,
        )
    )


def _fitted_logo_size_matches(
    size: dict[str, Any],
    *,
    pixel_width: int,
    pixel_height: int,
    box_width: int,
    box_height: int,
    tolerance: float,
) -> bool:
    width = _point_magnitude(size.get("width"))
    height = _point_magnitude(size.get("height"))
    if width is None or height is None:
        return False
    scale = min(box_width / pixel_width, box_height / pixel_height)
    return (
        abs(width - pixel_width * scale) <= tolerance
        and abs(height - pixel_height * scale) <= tolerance
    )


def _point_magnitude(value: object) -> float | None:
    if not isinstance(value, dict) or value.get("unit") != "PT":
        return None
    magnitude = value.get("magnitude")
    return float(magnitude) if isinstance(magnitude, int | float) else None
