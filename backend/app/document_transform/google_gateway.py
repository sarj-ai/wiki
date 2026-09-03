from __future__ import annotations

import asyncio
from collections.abc import Iterator
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

from app.document_transform.errors import DocumentGatewayError, UnsupportedSourceError
from app.document_transform.gateway import (
    DocumentGateway,
    OutputDraft,
    SourceSnapshot,
    TabHeader,
)
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
            raise DocumentGatewayError
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
