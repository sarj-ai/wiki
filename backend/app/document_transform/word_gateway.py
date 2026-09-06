from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
from http import HTTPStatus
from io import BytesIO
from typing import Any, Protocol
import uuid

from googleapiclient.http import MediaIoBaseUpload

from app.document_transform.errors import (
    DocumentGatewayError,
    OutputConflictError,
    OutputVerificationError,
    SourceChangedError,
    UnsupportedSourceError,
)
from app.document_transform.google_gateway import GoogleDocumentGateway
from app.document_transform.schemas import DocumentOutput, DriveFileSource
from app.document_transform.word_package import MAX_PACKAGE_BYTES
from app.drive.retry import is_retryable_provider_status
from app.files.errors import FilesError
from app.identifiers import FileId


WORD_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_FIELDS = (
    "id,name,parents,mimeType,modifiedTime,version,size,md5Checksum,"
    "webViewLink,trashed,appProperties,capabilities(canDownload)"
)
_OPERATION = "sarjWordTransformOperation"
_SOURCE = "sarjWordSourceSha256"
_OUTPUT = "sarjWordOutputSha256"


@dataclass(frozen=True, slots=True)
class WordSource:
    binding: dict[str, str]
    content: bytes


class WordGateway(Protocol):
    async def reserve_word_id(self) -> FileId: ...

    async def load_word(self, source: DriveFileSource) -> WordSource: ...

    async def recover_word(
        self, operation_id: uuid.UUID, *, source: WordSource, output_sha256: str, output_id: FileId
    ) -> DocumentOutput | None: ...

    async def create_word(
        self,
        operation_id: uuid.UUID,
        *,
        source: WordSource,
        content: bytes,
        name: str,
        output_id: FileId,
    ) -> DocumentOutput: ...

    async def verify_word(
        self,
        output: DocumentOutput,
        *,
        expected: bytes,
        operation_id: uuid.UUID,
        source: WordSource,
    ) -> None: ...


class GoogleWordGateway(GoogleDocumentGateway):
    async def reserve_word_id(self) -> FileId:
        return await asyncio.to_thread(self._reserve_word_id_sync)

    def _reserve_word_id_sync(self) -> FileId:
        response = self._execute_dict(
            self._drive().files().generateIds(count=1, space="drive", type="files")
        )
        ids = response.get("ids")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            raise DocumentGatewayError
        return FileId(ids[0])

    async def load_word(self, source: DriveFileSource) -> WordSource:
        try:
            file = await self._files.get(source.file_id)
        except FilesError as error:
            raise DocumentGatewayError(
                retryable=is_retryable_provider_status(error.http_status),
                http_status=error.http_status,
            ) from error
        if file.mime_type != WORD_MIME or not file.name.casefold().endswith(".docx"):
            raise UnsupportedSourceError
        loaded = await asyncio.to_thread(self._read_word_sync, source.file_id)
        if (
            loaded.binding["modifiedTime"] != source.expected_modified_time
            or loaded.binding["modifiedTime"] != file.modified_time
            or loaded.binding["name"] != file.name
            or loaded.binding["parent"] != file.parent_folder_id
        ):
            raise SourceChangedError
        return loaded

    async def recover_word(
        self, operation_id: uuid.UUID, *, source: WordSource, output_sha256: str, output_id: FileId
    ) -> DocumentOutput | None:
        return await asyncio.to_thread(
            self._recover_word_sync, operation_id, source, output_sha256, output_id
        )

    async def create_word(
        self,
        operation_id: uuid.UUID,
        *,
        source: WordSource,
        content: bytes,
        name: str,
        output_id: FileId,
    ) -> DocumentOutput:
        return await asyncio.to_thread(
            self._create_word_sync, operation_id, source, content, name, output_id
        )

    async def verify_word(
        self,
        output: DocumentOutput,
        *,
        expected: bytes,
        operation_id: uuid.UUID,
        source: WordSource,
    ) -> None:
        try:
            scoped = await self._files.get(output.file_id)
        except FilesError as error:
            if is_retryable_provider_status(error.http_status):
                raise DocumentGatewayError(retryable=True, http_status=error.http_status) from error
            raise OutputVerificationError from error
        if scoped.mime_type != WORD_MIME or scoped.parent_folder_id != output.parent_folder_id:
            raise OutputVerificationError
        observed = await asyncio.to_thread(self._read_word_sync, output.file_id)
        metadata = await asyncio.to_thread(self._word_metadata_sync, output.file_id)
        properties = _properties(operation_id, source.content, hashlib.sha256(expected).hexdigest())
        if _binding(metadata) != observed.binding or metadata.get("appProperties") != properties:
            raise OutputVerificationError
        if (
            observed.content != expected
            or observed.binding["parent"] != output.parent_folder_id
            or observed.binding["name"] != output.name
        ):
            raise OutputVerificationError

    def _word_metadata_sync(self, file_id: FileId) -> dict[str, Any]:
        return self._execute_dict(
            self._drive().files().get(fileId=file_id, supportsAllDrives=True, fields=_FIELDS)
        )

    def _read_word_sync(self, file_id: FileId) -> WordSource:
        before = _binding(self._word_metadata_sync(file_id))
        if before["id"] != file_id:
            raise SourceChangedError
        content = self._execute_bounded_bytes(
            self._drive().files().get_media(fileId=file_id, supportsAllDrives=True),
            max_bytes=MAX_PACKAGE_BYTES,
        )
        after = _binding(self._word_metadata_sync(file_id))
        if before != after:
            raise SourceChangedError
        if (
            len(content) != int(before["size"])
            or hashlib.md5(content, usedforsecurity=False).hexdigest() != before["md5Checksum"]
        ):
            raise SourceChangedError
        return WordSource(before, content)

    def _recover_word_sync(
        self, operation_id: uuid.UUID, source: WordSource, output_sha256: str, output_id: FileId
    ) -> DocumentOutput | None:
        try:
            metadata = self._word_metadata_sync(output_id)
        except DocumentGatewayError as error:
            if error.http_status == HTTPStatus.NOT_FOUND:
                return None
            raise
        expected_properties = _properties(operation_id, source.content, output_sha256)
        properties = metadata.get("appProperties")
        if not isinstance(properties, dict) or any(
            properties.get(key) != value for key, value in expected_properties.items()
        ):
            raise OutputConflictError
        output = _output(metadata)
        if (
            output.file_id != output_id
            or output.file_id == source.binding["id"]
            or output.parent_folder_id != source.binding["parent"]
        ):
            raise OutputConflictError
        return output

    def _create_word_sync(
        self,
        operation_id: uuid.UUID,
        source: WordSource,
        content: bytes,
        name: str,
        output_id: FileId,
    ) -> DocumentOutput:
        if _binding(self._word_metadata_sync(FileId(source.binding["id"]))) != source.binding:
            raise SourceChangedError
        try:
            metadata = self._execute_mutation_dict(
                self._drive()
                .files()
                .create(
                    body={
                        "id": output_id,
                        "name": name,
                        "mimeType": WORD_MIME,
                        "parents": [source.binding["parent"]],
                        "appProperties": _properties(
                            operation_id, source.content, hashlib.sha256(content).hexdigest()
                        ),
                    },
                    media_body=MediaIoBaseUpload(
                        BytesIO(content), mimetype=WORD_MIME, resumable=False
                    ),
                    supportsAllDrives=True,
                    fields=_FIELDS,
                )
            )
        except DocumentGatewayError as error:
            if error.http_status != HTTPStatus.CONFLICT:
                raise
            recovered = self._recover_word_sync(
                operation_id, source, hashlib.sha256(content).hexdigest(), output_id
            )
            if recovered is None:
                raise DocumentGatewayError(retryable=True) from error
            return recovered
        try:
            output = _output(metadata)
        except UnsupportedSourceError as error:
            # Upload was dispatched; only operation recovery may decide its outcome.
            raise DocumentGatewayError(retryable=True) from error
        if (
            output.file_id != output_id
            or output.file_id == source.binding["id"]
            or output.parent_folder_id != source.binding["parent"]
            or output.name != name
            or metadata.get("appProperties")
            != _properties(operation_id, source.content, hashlib.sha256(content).hexdigest())
        ):
            raise OutputConflictError
        return output


def _properties(operation_id: uuid.UUID, source: bytes, output_sha256: str) -> dict[str, str]:
    return {
        _OPERATION: str(operation_id),
        _SOURCE: hashlib.sha256(source).hexdigest(),
        _OUTPUT: output_sha256,
    }


def _binding(metadata: dict[str, Any]) -> dict[str, str]:
    keys = ("id", "name", "mimeType", "modifiedTime", "version", "size", "md5Checksum")
    if any(not isinstance(metadata.get(key), str) or not metadata[key] for key in keys):
        raise UnsupportedSourceError
    parents = metadata.get("parents")
    capabilities = metadata.get("capabilities")
    if not isinstance(parents, list) or len(parents) != 1 or not isinstance(parents[0], str):
        raise UnsupportedSourceError
    if not isinstance(capabilities, dict) or capabilities.get("canDownload") is not True:
        raise UnsupportedSourceError
    if (
        metadata["mimeType"] != WORD_MIME
        or metadata.get("trashed") is not False
        or not metadata["size"].isdigit()
        or not 0 < int(metadata["size"]) <= MAX_PACKAGE_BYTES
    ):
        raise UnsupportedSourceError
    return {**{key: metadata[key] for key in keys}, "parent": parents[0]}


def _output(metadata: dict[str, Any]) -> DocumentOutput:
    binding = _binding(metadata)
    return DocumentOutput(
        file_id=FileId(binding["id"]),
        name=binding["name"],
        parent_folder_id=FileId(binding["parent"]),
        media_type=WORD_MIME,
        web_url=metadata.get("webViewLink")
        if isinstance(metadata.get("webViewLink"), str)
        else None,
    )
