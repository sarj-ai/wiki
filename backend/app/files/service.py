from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, override

from googleapiclient.errors import HttpError

from app.drive.google_schemas import GoogleFile
from app.drive.retry import execute_mutation, execute_with_retry
from app.drive.schemas import MIME_FOLDER, MIME_HTML, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.drive.service import DriveService, reuse_drive_client
from app.files.errors import (
    CrossDriveMoveNotSupportedError,
    DriveUpstreamError,
    FileNotInScopeError,
    FolderCopyNotSupportedError,
    UnsupportedMimeError,
)
from app.files.schemas import DeleteResult, FileResponse, MoveResult
from app.identifiers import DriveId, FileId
from app.models.user import User


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource
    from googleapiclient._apis.drive.v3.schemas import File

_FIELDS = "id,name,mimeType,parents,driveId,modifiedTime,capabilities(canEdit)"
_MAX_HTML_BYTES = 5 * 1024 * 1024


class FilesService(ABC):
    @abstractmethod
    async def create(self, parent_folder_id: FileId, name: str, mime_type: str) -> FileResponse: ...

    @abstractmethod
    async def get(self, file_id: FileId) -> FileResponse: ...

    @abstractmethod
    async def can_edit(self, file_id: FileId) -> bool: ...

    @abstractmethod
    async def get_html_content(self, file_id: FileId) -> bytes: ...

    @abstractmethod
    async def rename(self, file_id: FileId, new_name: str) -> FileResponse: ...

    @abstractmethod
    async def move(self, file_id: FileId, new_parent_folder_id: FileId) -> MoveResult: ...

    @abstractmethod
    async def copy(self, file_id: FileId, new_name: str) -> FileResponse: ...

    @abstractmethod
    async def delete(self, file_id: FileId) -> DeleteResult: ...


class GoogleFilesService(FilesService):
    def __init__(self, *, user: User, drive: DriveService) -> None:
        self._user = user
        self._drive = drive
        self._clients: dict[int, DriveResource] = {}

    @override
    async def create(self, parent_folder_id: FileId, name: str, mime_type: str) -> FileResponse:
        if mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES:
            msg = f"Unsupported mimeType: {mime_type}"
            raise UnsupportedMimeError(msg)
        result = await asyncio.to_thread(self._create_sync, parent_folder_id, name, mime_type)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    @override
    async def get(self, file_id: FileId) -> FileResponse:
        result = await asyncio.to_thread(self._get_sync, file_id)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    @override
    async def can_edit(self, file_id: FileId) -> bool:
        result = await asyncio.to_thread(self._get_sync, file_id)
        await self._ensure_in_member_drive(result.drive_id)
        return result.can_edit

    @override
    async def get_html_content(self, file_id: FileId) -> bytes:
        file = await self.get(file_id)
        if file.mime_type != MIME_HTML:
            msg = "Only HTML files can use the isolated HTML viewer"
            raise UnsupportedMimeError(msg)
        await asyncio.to_thread(self._ensure_current_drive_membership_sync, file.drive_id)
        content = await asyncio.to_thread(self._download_sync, file_id)
        if len(content) > _MAX_HTML_BYTES:
            msg = "HTML file exceeds the 5 MiB viewer limit"
            raise UnsupportedMimeError(msg)
        return content

    @override
    async def rename(self, file_id: FileId, new_name: str) -> FileResponse:
        await self.get(file_id)
        return await asyncio.to_thread(self._rename_sync, file_id=file_id, new_name=new_name)

    @override
    async def move(self, file_id: FileId, new_parent_folder_id: FileId) -> MoveResult:
        source = await self.get(file_id)
        try:
            moved = await asyncio.to_thread(
                self._move_sync,
                file_id=file_id,
                new_parent_folder_id=new_parent_folder_id,
                prev_parent_folder_id=source.parent_folder_id,
            )
        except DriveUpstreamError as exc:
            if exc.http_status == 403:
                try:
                    destination = await self.get(new_parent_folder_id)
                except FileNotInScopeError:
                    raise exc from None
                if destination.drive_id != source.drive_id:
                    msg = "Drive blocked this cross-drive move — copy the file instead."
                    raise CrossDriveMoveNotSupportedError(msg) from exc
            raise
        await self._ensure_in_member_drive(moved.drive_id)
        return MoveResult(source=source, moved=moved)

    @override
    async def copy(self, file_id: FileId, new_name: str) -> FileResponse:
        source = await self.get(file_id)
        if source.mime_type == MIME_FOLDER:
            msg = "Folders can't be duplicated — copy individual files instead"
            raise FolderCopyNotSupportedError(msg)
        result = await asyncio.to_thread(self._copy_sync, file_id=file_id, new_name=new_name)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    @override
    async def delete(self, file_id: FileId) -> DeleteResult:
        source = await self.get(file_id)
        await asyncio.to_thread(self._trash_sync, file_id)
        return DeleteResult(source=source)

    async def _ensure_in_member_drive(self, drive_id: DriveId | None) -> None:
        if drive_id is None:
            msg = "File not available in Sarj Wiki"
            raise FileNotInScopeError(msg)
        drives = await self._drive.list_drives()
        if not any(d.id == drive_id for d in drives):
            msg = "File not available in Sarj Wiki"
            raise FileNotInScopeError(msg)

    def _create_sync(self, parent_folder_id: FileId, name: str, mime_type: str) -> FileResponse:
        client = self._client()
        body: File = {"name": name, "mimeType": mime_type, "parents": [parent_folder_id]}
        with _translated_drive_errors():
            raw = execute_mutation(
                client.files().create(body=body, fields=_FIELDS, supportsAllDrives=True)
            )
        return _to_response(raw)

    def _get_sync(self, file_id: FileId) -> FileResponse:
        client = self._client()
        with _translated_drive_errors():
            raw = execute_with_retry(
                client.files().get(fileId=file_id, fields=_FIELDS, supportsAllDrives=True)
            )
        return _to_response(raw)

    def _rename_sync(self, *, file_id: FileId, new_name: str) -> FileResponse:
        client = self._client()
        body: File = {"name": new_name}
        with _translated_drive_errors():
            raw = execute_with_retry(
                client.files().update(
                    fileId=file_id, body=body, fields=_FIELDS, supportsAllDrives=True
                )
            )
        return _to_response(raw)

    def _move_sync(
        self,
        *,
        file_id: FileId,
        new_parent_folder_id: FileId,
        prev_parent_folder_id: FileId | None,
    ) -> FileResponse:
        client = self._client()
        with _translated_drive_errors():
            if prev_parent_folder_id is None:
                meta = execute_with_retry(
                    client.files().get(fileId=file_id, fields="parents", supportsAllDrives=True)
                )
                remove_parents = ",".join(meta.get("parents", []))
            else:
                remove_parents = prev_parent_folder_id
            raw = execute_with_retry(
                client.files().update(
                    fileId=file_id,
                    addParents=new_parent_folder_id,
                    removeParents=remove_parents,
                    fields=_FIELDS,
                    supportsAllDrives=True,
                )
            )
        return _to_response(raw)

    def _copy_sync(self, *, file_id: FileId, new_name: str) -> FileResponse:
        client = self._client()
        body: File = {"name": new_name}
        with _translated_drive_errors():
            raw = execute_mutation(
                client.files().copy(
                    fileId=file_id, body=body, fields=_FIELDS, supportsAllDrives=True
                )
            )
        return _to_response(raw)

    def _trash_sync(self, file_id: FileId) -> None:
        client = self._client()
        body: File = {"trashed": True}
        with _translated_drive_errors():
            execute_with_retry(
                client.files().update(fileId=file_id, body=body, supportsAllDrives=True)
            )

    def _ensure_current_drive_membership_sync(self, drive_id: DriveId | None) -> None:
        if drive_id is None:
            msg = "File not available in Sarj Wiki"
            raise FileNotInScopeError(msg)
        client = self._client()
        with _translated_drive_errors():
            raw = execute_with_retry(client.drives().get(driveId=drive_id, fields="id,hidden"))
        if raw.get("id") != drive_id or raw.get("hidden") is True:
            msg = "File not available in Sarj Wiki"
            raise FileNotInScopeError(msg)

    def _download_sync(self, file_id: FileId) -> bytes:
        client = self._client()
        with _translated_drive_errors():
            raw = execute_with_retry(
                client.files().get_media(fileId=file_id, supportsAllDrives=True)
            )
        if not isinstance(raw, bytes):
            msg = "Google Drive returned invalid HTML content"
            raise DriveUpstreamError(502, msg)
        return raw

    def _client(self) -> DriveResource:
        return reuse_drive_client(self._user, self._clients)


def _to_response(raw: File) -> FileResponse:
    file = GoogleFile.model_validate(raw)
    return FileResponse(
        id=file.id,
        name=file.name,
        mime_type=file.mime_type,
        parent_folder_id=file.parents[0] if file.parents else None,
        drive_id=file.drive_id,
        modified_time=file.modified_time,
        can_edit=file.capabilities.can_edit,
    )


@contextmanager
def _translated_drive_errors() -> Generator[None]:
    try:
        yield
    except HttpError as e:
        raise _drive_upstream(e) from e


def _drive_upstream(exc: HttpError) -> DriveUpstreamError:
    code = exc.resp.status if exc.resp else 502
    detail = str(exc) or "Drive request failed"
    return DriveUpstreamError(code, detail)
