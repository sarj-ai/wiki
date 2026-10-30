from __future__ import annotations

from collections.abc import Callable
import hashlib
from http import HTTPStatus
from io import BytesIO
from typing import Any, override
import uuid
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest
from httplib2 import Response
import pytest

from app.document_transform.errors import (
    DocumentGatewayError,
    IncompatiblePendingOutputError,
    SourceChangedError,
    SourceTooLargeError,
    UnsupportedSourceError,
)
from app.document_transform.google_presentation_gateway import (
    PPTX_MIME_TYPE,
    GooglePresentationGateway,
)
from app.document_transform.presentation_gateway import (
    PresentationDraft,
    PresentationFingerprints,
    PresentationRevisionId,
)
from app.document_transform.schemas import DriveFileSource
from app.drive.retry import ProviderDeadlineError
from app.drive.schemas import MIME_FOLDER, MIME_SLIDE
from app.files.errors import DriveUpstreamError, FileNotInScopeError
from app.files.schemas import DeleteResult, FileResponse, MoveResult
from app.files.service import FilesService
from app.identifiers import FileId, GoogleUserId
from app.models.user import User


class _Request(HttpRequest):
    def __init__(self, callback: Callable[[], object]) -> None:
        self._callback = callback
        self.headers = {}

    @override
    def execute(self, http: object = None, num_retries: int = 0) -> object:
        del http, num_retries
        return self._callback()


class _FilesBoundary:
    def __init__(self, state: _GoogleState) -> None:
        self._state = state

    def get(self, **kwargs: object) -> HttpRequest:
        file_id = str(kwargs["fileId"])
        return _Request(lambda: dict(self._state.metadata[file_id]))

    def get_media(self, **kwargs: object) -> HttpRequest:
        file_id = str(kwargs["fileId"])
        request = _Request(lambda: self._state.media[file_id])
        self._state.media_requests.append(request)
        return request

    def list(self, **kwargs: object) -> HttpRequest:
        query = str(kwargs["q"])

        def execute() -> object:
            if "appProperties has" not in query:
                return {"files": []}
            operation_id = query.split("value='", 1)[1].split("'", 1)[0]
            return {
                "files": [
                    dict(metadata)
                    for metadata in self._state.metadata.values()
                    if metadata.get("appProperties", {}).get("sarjTransformOperation")
                    == operation_id
                ]
            }

        return _Request(execute)

    def copy(self, **kwargs: object) -> HttpRequest:
        self._state.copy_calls.append(kwargs)

        def execute() -> object:
            source_id = str(kwargs["fileId"])
            body = kwargs["body"]
            assert isinstance(body, dict)
            output = self._state.add_output(source_id, body)
            if self._state.copy_loses_response:
                raise ProviderDeadlineError
            return output

        return _Request(execute)

    def create(self, **kwargs: object) -> HttpRequest:
        self._state.create_calls.append(kwargs)

        def execute() -> object:
            if self._state.import_http_error_status is not None:
                raise HttpError(
                    Response({"status": str(self._state.import_http_error_status)}),
                    b"invalid presentation",
                )
            body = kwargs["body"]
            assert isinstance(body, dict)
            output = self._state.add_output("source", body)
            output["mimeType"] = MIME_SLIDE
            self._state.metadata[str(output["id"])]["mimeType"] = MIME_SLIDE
            return output

        return _Request(execute)

    def update(self, **kwargs: object) -> HttpRequest:
        self._state.update_calls.append(kwargs)

        def execute() -> object:
            file_id = str(kwargs["fileId"])
            body = kwargs["body"]
            assert isinstance(body, dict)
            properties = body.get("appProperties")
            assert isinstance(properties, dict)
            if self._state.fail_mutation_property_updates and "sarjTransformMutation" in properties:
                raise ProviderDeadlineError
            current = self._state.metadata[file_id].setdefault("appProperties", {})
            assert isinstance(current, dict)
            current.update(properties)
            return {"id": file_id}

        return _Request(execute)


class _DriveBoundary:
    def __init__(self, state: _GoogleState) -> None:
        self._files = _FilesBoundary(state)
        self._about = _AboutBoundary(state)

    def files(self) -> object:
        return self._files

    def about(self) -> object:
        return self._about


class _AboutBoundary:
    def __init__(self, state: _GoogleState) -> None:
        self._state = state

    def get(self, **kwargs: object) -> HttpRequest:
        del kwargs
        return _Request(
            lambda: {
                "importFormats": (
                    {PPTX_MIME_TYPE: [MIME_SLIDE]} if self._state.import_supported else {}
                )
            }
        )


class _PresentationsBoundary:
    def __init__(self, state: _GoogleState) -> None:
        self._state = state

    def get(self, **kwargs: object) -> HttpRequest:
        presentation_id = str(kwargs["presentationId"])
        return _Request(lambda: dict(self._state.presentations[presentation_id]))

    def __getattr__(self, name: str) -> object:
        if name == "batchUpdate":
            return self.batch_update
        raise AttributeError(name)

    def batch_update(self, **kwargs: object) -> HttpRequest:
        self._state.batch_calls.append(kwargs)

        def execute() -> object:
            presentation_id = str(kwargs["presentationId"])
            body = kwargs["body"]
            assert isinstance(body, dict)
            write_control = body.get("writeControl")
            assert isinstance(write_control, dict)
            presentation = self._state.presentations[presentation_id]
            if write_control.get("requiredRevisionId") != presentation["revisionId"]:
                raise DocumentGatewayError
            presentation.update(
                {
                    "revisionId": "revision-after",
                    "semantic": "semantic-after",
                    "protected": "protected-after",
                    "brand": "brand-after",
                }
            )
            if self._state.batch_loses_response:
                raise ProviderDeadlineError
            return {"writeControl": {"requiredRevisionId": "revision-after"}}

        return _Request(execute)


class _SlidesBoundary:
    def __init__(self, state: _GoogleState) -> None:
        self._presentations = _PresentationsBoundary(state)

    def presentations(self) -> object:
        return self._presentations


class _GoogleState:
    def __init__(self, *, mime_type: str = MIME_SLIDE, media: bytes = b"") -> None:
        self.metadata: dict[str, dict[str, Any]] = {
            "folder": {
                "id": "folder",
                "name": "Wiki",
                "mimeType": MIME_FOLDER,
                "parents": [],
                "modifiedTime": "2026-09-03T11:00:00Z",
                "version": "4",
                "capabilities": {
                    "canAddChildren": True,
                    "canEdit": True,
                },
                "appProperties": {},
            },
            "source": {
                "id": "source",
                "name": "Quarterly Review.pptx" if mime_type == PPTX_MIME_TYPE else "Review",
                "mimeType": mime_type,
                "parents": ["folder"],
                "modifiedTime": "2026-09-03T12:00:00Z",
                "version": "17",
                "size": str(len(media)),
                "md5Checksum": (
                    hashlib.md5(media, usedforsecurity=False).hexdigest()
                    if mime_type == PPTX_MIME_TYPE
                    else None
                ),
                "capabilities": {
                    "canCopy": mime_type == MIME_SLIDE,
                    "canDownload": mime_type == PPTX_MIME_TYPE,
                    "canEdit": True,
                },
                "appProperties": {},
            },
        }
        self.presentations: dict[str, dict[str, Any]] = {}
        if mime_type == MIME_SLIDE:
            self.presentations["source"] = {
                "presentationId": "source",
                "revisionId": "revision-source",
                "semantic": "semantic-source",
                "protected": "protected-source",
                "brand": None,
            }
        self.media = {"source": media}
        self.copy_calls: list[dict[str, object]] = []
        self.create_calls: list[dict[str, object]] = []
        self.update_calls: list[dict[str, object]] = []
        self.batch_calls: list[dict[str, object]] = []
        self.media_requests: list[_Request] = []
        self.copy_loses_response = False
        self.batch_loses_response = False
        self.import_supported = True
        self.import_http_error_status: int | None = None
        self.fail_mutation_property_updates = False
        self.out_of_scope: set[str] = set()
        self.insufficient_scope: set[str] = set()
        self.transient_failure: set[str] = set()

    def add_output(self, source_id: str, body: dict[str, Any]) -> dict[str, Any]:
        file_id = "output"
        assert source_id in self.metadata
        output = {
            "id": file_id,
            "name": body["name"],
            "mimeType": MIME_SLIDE,
            "parents": list(body["parents"]),
            "modifiedTime": "2026-09-03T12:01:00Z",
            "version": "18",
            "capabilities": {
                "canCopy": True,
                "canDownload": False,
                "canEdit": True,
            },
            "webViewLink": "https://docs.google.com/presentation/d/output",
            "appProperties": dict(body["appProperties"]),
        }
        self.metadata[file_id] = output
        self.presentations[file_id] = {
            "presentationId": file_id,
            "revisionId": "revision-output",
            "semantic": "semantic-before",
            "protected": "protected-before",
            "brand": None,
        }
        return dict(output)


class _FilesService(FilesService):
    def __init__(self, state: _GoogleState) -> None:
        self._state = state

    @override
    async def create(self, parent_folder_id: FileId, name: str, mime_type: str) -> FileResponse:
        raise NotImplementedError

    @override
    async def get(self, file_id: FileId) -> FileResponse:
        if str(file_id) in self._state.out_of_scope:
            raise FileNotInScopeError
        if str(file_id) in self._state.transient_failure:
            raise DriveUpstreamError(502)
        if str(file_id) in self._state.insufficient_scope:
            raise DriveUpstreamError(403, reason="insufficientPermissions")
        raw = self._state.metadata[str(file_id)]
        parents = raw.get("parents")
        capabilities = raw.get("capabilities")
        assert isinstance(parents, list)
        assert isinstance(capabilities, dict)
        return FileResponse(
            id=FileId(str(raw["id"])),
            name=str(raw["name"]),
            mime_type=str(raw["mimeType"]),
            parent_folder_id=FileId(str(parents[0])) if parents else None,
            drive_id=None,
            modified_time=str(raw["modifiedTime"]),
            can_edit=capabilities.get("canEdit") is True,
            can_add_children=capabilities.get("canAddChildren") is True,
        )

    @override
    async def can_edit(self, file_id: FileId) -> bool:
        return str(file_id) in self._state.metadata

    @override
    async def get_html_content(self, file_id: FileId) -> bytes:
        raise NotImplementedError

    @override
    async def rename(self, file_id: FileId, new_name: str) -> FileResponse:
        raise NotImplementedError

    @override
    async def move(self, file_id: FileId, new_parent_folder_id: FileId) -> MoveResult:
        raise NotImplementedError

    @override
    async def copy(self, file_id: FileId, new_name: str) -> FileResponse:
        raise NotImplementedError

    @override
    async def delete(self, file_id: FileId) -> DeleteResult:
        raise NotImplementedError


def _user() -> User:
    return User(
        email="user@sarj.ai",
        google_id=GoogleUserId("google-subject"),
        display_name="User",
    )


def _fingerprint(
    presentation: dict[str, Any],
    title: str,
) -> PresentationFingerprints:
    del title
    semantic = presentation["semantic"]
    protected = presentation["protected"]
    brand = presentation["brand"]
    assert isinstance(semantic, str)
    assert isinstance(protected, str)
    assert brand is None or isinstance(brand, str)
    return PresentationFingerprints(
        semantic_sha256=semantic,
        protected_structure_sha256=protected,
        brand_sha256=brand,
    )


def _gateway(state: _GoogleState) -> GooglePresentationGateway:
    return GooglePresentationGateway(
        user=_user(),
        files=_FilesService(state),
        fingerprint=_fingerprint,
        drive_provider=_DriveBoundary(state),
        slides_provider=_SlidesBoundary(state),
    )


def _source_request() -> DriveFileSource:
    return DriveFileSource(
        kind="drive_file",
        file_id=FileId("source"),
        expected_modified_time="2026-09-03T12:00:00Z",
    )


async def _apply_standard_mutation(
    gateway: GooglePresentationGateway,
    draft: PresentationDraft,
    *,
    requests: list[dict[str, Any]] | None = None,
    expected_semantic_sha256: str = "semantic-after",
    expected_brand_sha256: str | None = "brand-after",
) -> PresentationDraft:
    return await gateway.apply_requests_once(
        draft=draft,
        mutation_sha256="mutation-hash",
        requests=requests or [{"replaceAllText": {"containsText": {"text": "unused"}}}],
        required_revision_id=PresentationRevisionId("revision-output"),
        expected_semantic_sha256=expected_semantic_sha256,
        expected_protected_structure_sha256="protected-after",
        expected_brand_sha256=expected_brand_sha256,
    )


def _set_attribute(instance: object, attribute: str, value: object) -> None:
    setattr(instance, attribute, value)


def _pptx(
    *,
    content_types: bytes = (
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    ),
    presentation: bytes = (
        b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    ),
    extra_name: str | None = None,
    extra_payload: bytes = b"bad",
) -> bytes:
    target = BytesIO()
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("ppt/presentation.xml", presentation)
        if extra_name is not None:
            archive.writestr(extra_name, extra_payload)
    return target.getvalue()


def _pptx_with_unsupported_compression() -> bytes:
    raw = bytearray(_pptx())
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        index = 0
        while True:
            index = raw.find(signature, index)
            if index < 0:
                break
            raw[index + offset : index + offset + 2] = (99).to_bytes(2, "little")
            index += len(signature)
    return bytes(raw)


def test_property_update_constructs_one_drive_request() -> None:
    state = _GoogleState()
    gateway = _gateway(state)

    gateway._update_properties_sync(FileId("source"), {"checkpoint": "ready"})

    assert state.update_calls == [
        {
            "fileId": "source",
            "supportsAllDrives": True,
            "body": {"appProperties": {"checkpoint": "ready"}},
            "fields": "id",
        }
    ]
    assert state.metadata["source"]["appProperties"] == {"checkpoint": "ready"}


async def test_native_copy_is_same_parent_tracked_and_recovers_lost_response() -> None:
    state = _GoogleState()
    state.copy_loses_response = True
    gateway = _gateway(state)
    operation_id = uuid.uuid7()
    source = await gateway.load_source(_source_request())

    draft = await gateway.create_copy(
        operation_id=operation_id,
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )

    assert draft.file_id == "output"
    assert draft.parent_folder_id == "folder"
    assert len(state.copy_calls) == 1
    body = state.copy_calls[0]["body"]
    assert isinstance(body, dict)
    assert body["parents"] == ["folder"]
    assert body["appProperties"] == {
        "sarjTransformOperation": str(operation_id),
        "sarjTransformSourceHash": "source-hash",
        "sarjTransformState": "incomplete",
    }


async def test_stale_requested_modified_time_is_rejected_before_source_read() -> None:
    state = _GoogleState()
    gateway = _gateway(state)

    with pytest.raises(SourceChangedError):
        await gateway.load_source(
            DriveFileSource(
                kind="drive_file",
                file_id=FileId("source"),
                expected_modified_time="2026-09-03T11:59:00Z",
            )
        )


async def test_out_of_scope_source_is_terminal_unsupported() -> None:
    state = _GoogleState()
    state.out_of_scope.add("source")

    with pytest.raises(UnsupportedSourceError):
        await _gateway(state).load_source(_source_request())


async def test_insufficient_google_scope_is_terminal_unsupported() -> None:
    state = _GoogleState()
    state.insufficient_scope.add("source")

    with pytest.raises(UnsupportedSourceError):
        await _gateway(state).load_source(_source_request())


async def test_native_revision_change_aborts_before_copy() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    state.presentations["source"]["revisionId"] = "collaborator-revision"

    with pytest.raises(SourceChangedError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="source-hash",
            output_name="Review — Sarj",
        )

    assert state.copy_calls == []


async def test_drive_version_change_aborts_before_copy() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    state.metadata["source"]["version"] = "18"

    with pytest.raises(SourceChangedError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="source-hash",
            output_name="Review — Sarj",
        )

    assert state.copy_calls == []


async def test_transient_source_revalidation_failure_is_retryable() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    state.transient_failure.add("source")

    with pytest.raises(DocumentGatewayError):
        await gateway.source_is_unchanged(source)


@pytest.mark.parametrize(
    ("mime_type", "name", "capability"),
    [
        (MIME_SLIDE, "Review", "canCopy"),
        (PPTX_MIME_TYPE, "Review.pptx", "canDownload"),
        (PPTX_MIME_TYPE, "Review.pptm", None),
    ],
    ids=["native-cannot-copy", "pptx-cannot-download", "extension-mismatch"],
)
async def test_source_capability_and_extension_fences(
    mime_type: str,
    name: str,
    capability: str | None,
) -> None:
    state = _GoogleState(mime_type=mime_type, media=_pptx())
    state.metadata["source"]["name"] = name
    if capability is not None:
        state.metadata["source"]["capabilities"][capability] = False
    gateway = _gateway(state)

    with pytest.raises(UnsupportedSourceError):
        await gateway.load_source(_source_request())


async def test_pptx_import_validates_archive_and_creates_native_slides() -> None:
    media = _pptx()
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=media)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    draft = await gateway.create_copy(
        operation_id=uuid.uuid7(),
        source=source,
        source_sha256="pptx-hash",
        output_name="Quarterly Review — Sarj",
    )

    assert draft.file_id == "output"
    assert len(state.create_calls) == 1
    body = state.create_calls[0]["body"]
    assert isinstance(body, dict)
    assert body["mimeType"] == MIME_SLIDE
    assert body["parents"] == ["folder"]
    headers = state.media_requests[0].headers
    assert headers is not None
    assert headers["Range"] == f"bytes=0-{25 * 1024 * 1024}"


@pytest.mark.parametrize("mime_type", [MIME_SLIDE, PPTX_MIME_TYPE])
async def test_destination_is_revalidated_writable_immediately_before_create(
    mime_type: str,
) -> None:
    media = _pptx() if mime_type == PPTX_MIME_TYPE else b""
    state = _GoogleState(mime_type=mime_type, media=media)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    capabilities = state.metadata["folder"]["capabilities"]
    assert isinstance(capabilities, dict)
    capabilities["canAddChildren"] = False

    with pytest.raises(UnsupportedSourceError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="source-hash",
            output_name="Denied",
        )

    assert state.copy_calls == []
    assert state.create_calls == []


async def test_pptx_import_requires_drive_conversion_support_before_download() -> None:
    media = _pptx()
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=media)
    state.import_supported = False
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(UnsupportedSourceError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Quarterly Review — Sarj",
        )

    assert state.media_requests == []
    assert state.create_calls == []


@pytest.mark.parametrize(
    "raw",
    [
        b"not a zip",
        _pptx(extra_name="../escape.xml"),
        _pptx(extra_name="/absolute.xml"),
        _pptx(extra_name="ppt/vbaProject.bin"),
        _pptx(extra_name="activeX/activeX1.bin"),
        _pptx(extra_name="ppt/slides/media/EmBeDdInGs/payload.bin"),
        _pptx(extra_name="OLEOBJECTS/object.bin"),
        _pptx(
            extra_name="ppt/_rels/presentation.xml.rels",
            extra_payload=(
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                b'relationships"><Relationship Id="rId1" '
                b'Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                b'relationships/image" TargetMode="External" '
                b'Target="https://bad.example/tracker.png"/></Relationships>'
            ),
        ),
        _pptx(content_types=b"not xml"),
        _pptx(presentation=b"not xml"),
        _pptx(
            content_types=(
                '<?xml version="1.0" encoding="UTF-16"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Override PartName="/ppt/presentation.xml" '
                'ContentType="application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"/>'
                "</Types>"
            ).encode("utf-16")
        ),
    ],
    ids=[
        "not-zip",
        "parent-traversal",
        "absolute-path",
        "macro-part",
        "root-active-x-part",
        "nested-mixed-case-embedding-part",
        "root-mixed-case-ole-part",
        "external-media",
        "malformed-content-types",
        "malformed-presentation",
        "utf16-macro-content-type",
    ],
)
async def test_pptx_archive_rejects_unsafe_content_through_public_import(raw: bytes) -> None:
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=raw)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(UnsupportedSourceError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Unsafe",
        )

    assert state.create_calls == []


async def test_oversized_relationship_is_rejected_before_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _pptx(
        extra_name="ppt/slides/_rels/slide1.xml.rels",
        extra_payload=b" " * (1024 * 1024 + 1),
    )
    original_read = ZipFile.read
    relationship_read = False

    def guarded_read(
        archive: ZipFile,
        member: str | ZipInfo,
        pwd: bytes | None = None,
    ) -> bytes:
        nonlocal relationship_read
        name = member.filename if isinstance(member, ZipInfo) else member
        if name.endswith(".rels"):
            relationship_read = True
        return original_read(archive, member, pwd)

    monkeypatch.setattr(ZipFile, "read", guarded_read)
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=raw)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(UnsupportedSourceError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Compressed bomb",
        )

    assert not relationship_read
    assert state.create_calls == []


async def test_pptx_archive_rejects_unsupported_compression_as_unsupported_source() -> None:
    raw = _pptx_with_unsupported_compression()
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=raw)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(UnsupportedSourceError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Unsupported compression",
        )

    assert state.create_calls == []


@pytest.mark.parametrize(
    "relationship_type",
    [
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/hyperlink",
    ],
    ids=["transitional", "strict"],
)
async def test_pptx_archive_allows_standard_https_hyperlinks(
    relationship_type: str,
) -> None:
    relationships = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="{relationship_type}"
    Target="https://sarj.ai/review" TargetMode="External"/>
</Relationships>""".encode()
    raw = _pptx(
        extra_name="ppt/slides/_rels/slide1.xml.rels",
        extra_payload=relationships,
    )
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=raw)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    draft = await gateway.create_copy(
        operation_id=uuid.uuid7(),
        source=source,
        source_sha256="pptx-hash",
        output_name="Linked deck",
    )

    assert draft.file_id == "output"
    assert len(state.create_calls) == 1


@pytest.mark.parametrize(
    "relationships",
    [
        (
            b'<Relationships><Relationship Type="https://attacker.invalid/custom/hyperlink" '
            b'TargetMode="External" Target="https://example.com"/></Relationships>'
        ),
        (
            b'<Relationships><Relationship Type="http://schemas.openxmlformats.org/'
            b'officeDocument/2006/relationships/image" TargetMode=" External " '
            b'Target="https://example.com/tracker.png"/></Relationships>'
        ),
    ],
    ids=["invented-hyperlink-type", "invalid-target-mode"],
)
async def test_pptx_archive_rejects_relationship_allowlist_bypasses(
    relationships: bytes,
) -> None:
    raw = _pptx(
        extra_name="ppt/slides/_rels/slide1.xml.rels",
        extra_payload=relationships,
    )
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=raw)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(UnsupportedSourceError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Unsafe relationship",
        )

    assert state.create_calls == []


async def test_pptx_import_bad_request_is_terminal_unsupported() -> None:
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=_pptx())
    state.import_http_error_status = HTTPStatus.BAD_REQUEST
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(UnsupportedSourceError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Invalid import",
        )

    assert len(state.create_calls) == 1


async def test_pptx_download_rejects_more_than_25_mib_before_media_request() -> None:
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=b"")
    state.metadata["source"]["size"] = str(25 * 1024 * 1024 + 1)
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(SourceTooLargeError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Too large",
        )

    assert state.create_calls == []


async def test_pptx_download_rejects_bytes_that_do_not_match_drive_md5() -> None:
    state = _GoogleState(mime_type=PPTX_MIME_TYPE, media=_pptx())
    state.metadata["source"]["md5Checksum"] = "0" * 32
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())

    with pytest.raises(SourceChangedError):
        await gateway.create_copy(
            operation_id=uuid.uuid7(),
            source=source,
            source_sha256="pptx-hash",
            output_name="Changed",
        )

    assert state.create_calls == []


async def test_revision_fenced_batch_recovers_exact_ambiguous_postcondition() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    draft = await gateway.create_copy(
        operation_id=uuid.uuid7(),
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    state.batch_loses_response = True

    applied = await _apply_standard_mutation(gateway, draft)

    assert applied.revision_id == "revision-after"
    assert applied.applied_mutation_sha256 == "mutation-hash"
    assert len(state.batch_calls) == 1
    body = state.batch_calls[0]["body"]
    assert isinstance(body, dict)
    assert body["writeControl"] == {"requiredRevisionId": "revision-output"}
    properties = state.metadata["output"]["appProperties"]
    assert properties["sarjTransformPendingMutation"] == "mutation-hash"
    assert (
        properties["sarjTransformMutationBaseHash"]
        == hashlib.sha256(b"revision-output").hexdigest()
    )
    assert properties["sarjTransformMutation"] == "mutation-hash"
    assert properties["sarjTransformExpectedSemanticHash"] == "semantic-after"
    assert properties["sarjTransformExpectedProtectedHash"] == "protected-after"
    assert properties["sarjTransformExpectedBrandHash"] == "brand-after"


async def test_pending_commit_is_reconciled_after_mutation_property_update_is_lost() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    operation_id = uuid.uuid7()
    source = await gateway.load_source(_source_request())
    draft = await gateway.create_copy(
        operation_id=operation_id,
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    state.fail_mutation_property_updates = True

    with pytest.raises(ProviderDeadlineError):
        await _apply_standard_mutation(gateway, draft)

    state.fail_mutation_property_updates = False
    recovered = await gateway.recover_output(
        operation_id,
        expected_parent_folder_id=FileId("folder"),
    )
    assert recovered is not None
    reconciled = await gateway.reconcile_pending(recovered)

    assert reconciled.applied_mutation_sha256 == "mutation-hash"
    assert reconciled.revision_id == "revision-after"
    assert len(state.batch_calls) == 1


async def test_pending_mismatch_with_changed_revision_is_not_retried() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    operation_id = uuid.uuid7()
    source = await gateway.load_source(_source_request())
    await gateway.create_copy(
        operation_id=operation_id,
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    properties = state.metadata["output"]["appProperties"]
    assert isinstance(properties, dict)
    properties.update(
        {
            "sarjTransformPendingMutation": "mutation-hash",
            "sarjTransformMutationBaseHash": hashlib.sha256(b"revision-output").hexdigest(),
            "sarjTransformExpectedSemanticHash": "semantic-after",
            "sarjTransformExpectedProtectedHash": "protected-after",
            "sarjTransformExpectedBrandHash": "brand-after",
        }
    )
    state.presentations["output"]["revisionId"] = "collaborator-revision"
    recovered = await gateway.recover_output(
        operation_id, expected_parent_folder_id=FileId("folder")
    )
    assert recovered is not None

    with pytest.raises(SourceChangedError):
        await gateway.reconcile_pending(recovered)


async def test_applied_marker_cannot_override_known_postcondition_mismatch() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    operation_id = uuid.uuid7()
    source = await gateway.load_source(_source_request())
    await gateway.create_copy(
        operation_id=operation_id,
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    properties = state.metadata["output"]["appProperties"]
    assert isinstance(properties, dict)
    properties.update(
        {
            "sarjTransformMutation": "mutation-hash",
            "sarjTransformPendingMutation": "mutation-hash",
            "sarjTransformMutationBaseHash": hashlib.sha256(b"revision-output").hexdigest(),
            "sarjTransformExpectedSemanticHash": "semantic-after",
            "sarjTransformExpectedProtectedHash": "protected-after",
            "sarjTransformExpectedBrandHash": "brand-after",
        }
    )
    recovered = await gateway.recover_output(
        operation_id, expected_parent_folder_id=FileId("folder")
    )
    assert recovered is not None

    with pytest.raises(IncompatiblePendingOutputError):
        await gateway.reconcile_pending(recovered)


async def test_ambiguous_batch_with_different_postcondition_needs_review() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    draft = await gateway.create_copy(
        operation_id=uuid.uuid7(),
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    state.batch_loses_response = True

    with pytest.raises(SourceChangedError):
        await _apply_standard_mutation(
            gateway,
            draft,
            requests=[{"deleteText": {"objectId": "shape-1"}}],
            expected_semantic_sha256="different-semantic",
            expected_brand_sha256=None,
        )

    assert "sarjTransformMutation" not in state.metadata["output"]["appProperties"]


async def test_applied_mutation_replay_does_not_send_another_batch() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    draft = await gateway.create_copy(
        operation_id=uuid.uuid7(),
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    applied = PresentationDraft(
        file_id=draft.file_id,
        name=draft.name,
        parent_folder_id=draft.parent_folder_id,
        revision_id=draft.revision_id,
        source_sha256=draft.source_sha256,
        applied_mutation_sha256="mutation-hash",
    )

    replayed = await _apply_standard_mutation(
        gateway,
        applied,
        requests=[{"deleteText": {"objectId": "shape-1"}}],
        expected_brand_sha256=None,
    )

    assert replayed is applied
    assert state.batch_calls == []


async def test_brand_verification_reports_mismatch() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    source = await gateway.load_source(_source_request())
    draft = await gateway.create_copy(
        operation_id=uuid.uuid7(),
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )

    verification = await gateway.verify_output(
        draft=draft,
        title=draft.name,
        expected_semantic_sha256="semantic-before",
        expected_protected_structure_sha256="protected-before",
        expected_brand_sha256="required-brand",
    )
    assert verification.content_preserved
    assert verification.structure_preserved
    assert not verification.brand_applied
    assert state.metadata["output"]["appProperties"]["sarjTransformState"] == "incomplete"


async def test_recovery_rejects_multiple_outputs_for_one_operation() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    operation_id = uuid.uuid7()
    source = await gateway.load_source(_source_request())
    await gateway.create_copy(
        operation_id=operation_id,
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    duplicate = dict(state.metadata["output"])
    duplicate["id"] = "duplicate"
    state.metadata["duplicate"] = duplicate
    state.presentations["duplicate"] = dict(state.presentations["output"])

    with pytest.raises(DocumentGatewayError):
        await gateway.recover_output(
            operation_id,
            expected_parent_folder_id=FileId("folder"),
        )


async def test_recovery_rejects_output_outside_exact_parent_or_owner_scope() -> None:
    state = _GoogleState()
    gateway = _gateway(state)
    operation_id = uuid.uuid7()
    source = await gateway.load_source(_source_request())
    await gateway.create_copy(
        operation_id=operation_id,
        source=source,
        source_sha256="source-hash",
        output_name="Review — Sarj",
    )
    state.metadata["output"]["parents"] = ["other-folder"]

    with pytest.raises(UnsupportedSourceError):
        await gateway.recover_output(
            operation_id,
            expected_parent_folder_id=FileId("folder"),
        )

    state.metadata["output"]["parents"] = ["folder"]
    state.out_of_scope.add("output")
    with pytest.raises(UnsupportedSourceError):
        await gateway.recover_output(
            operation_id,
            expected_parent_folder_id=FileId("folder"),
        )


def test_presentation_draft_is_immutable() -> None:
    draft = PresentationDraft(
        file_id=FileId("output"),
        name="Review",
        parent_folder_id=FileId("folder"),
        revision_id=PresentationRevisionId("revision"),
        source_sha256="source-hash",
    )

    with pytest.raises(AttributeError):
        _set_attribute(draft, "name", "Changed")
