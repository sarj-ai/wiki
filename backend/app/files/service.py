"""File CRUD service.

Everything here uses the caller's own OAuth token — no service account.
HTTP 4xx from Drive surfaces with the same status; 5xx and transport
errors bubble as 502 — both via :class:`DriveUpstreamError`.

Strict-mode scoping
-------------------

Every file Sarj Wiki reads or writes must live in a shared drive
the caller is a direct member of. A file the caller's token can see via
``files.get`` could also be in their personal My Drive or in a shared
drive they have one-off share access to but aren't a member of — both
fall outside the sidebar's visible surface. ``_ensure_in_member_drive``
runs after every metadata fetch and raises :class:`FileNotInScopeError`
(404, not 403, to avoid leaking existence) for anything outside a
member drive.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, override

from googleapiclient.errors import HttpError

from app.drive.google_schemas import GoogleFile
from app.drive.retry import execute_mutation, execute_with_retry
from app.drive.schemas import MIME_FOLDER, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.drive.service import DriveService, GoogleDriveService
from app.files.errors import (
    CrossDriveMoveNotSupportedError,
    DriveUpstreamError,
    FileNotInScopeError,
    FolderCopyNotSupportedError,
    UnsupportedMimeError,
)
from app.files.schemas import DeleteResult, FileResponse, MoveResult
from app.models.user import User


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource
    from googleapiclient._apis.drive.v3.schemas import File

_FIELDS = "id,name,mimeType,parents,driveId,modifiedTime"


class FilesService(ABC):
    """Contract for Drive file CRUD against a member shared drive."""

    @abstractmethod
    async def create(self, parent_folder_id: str, name: str, mime_type: str) -> FileResponse: ...

    @abstractmethod
    async def get(self, file_id: str) -> FileResponse: ...

    @abstractmethod
    async def rename(self, file_id: str, new_name: str) -> FileResponse: ...

    @abstractmethod
    async def move(self, file_id: str, new_parent_folder_id: str) -> MoveResult: ...

    @abstractmethod
    async def copy(self, file_id: str, new_name: str) -> FileResponse: ...

    @abstractmethod
    async def delete(self, file_id: str) -> DeleteResult: ...


class GoogleFilesService(FilesService):
    """``FilesService`` backed by the real Drive v3 API."""

    def __init__(self, *, user: User, drive: DriveService) -> None:
        self._user = user
        self._drive = drive

    @override
    async def create(self, parent_folder_id: str, name: str, mime_type: str) -> FileResponse:
        if mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES:
            raise UnsupportedMimeError(f"Unsupported mimeType: {mime_type}")
        result = await asyncio.to_thread(self._create_sync, parent_folder_id, name, mime_type)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    @override
    async def get(self, file_id: str) -> FileResponse:
        result = await asyncio.to_thread(self._get_sync, file_id)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    @override
    async def rename(self, file_id: str, new_name: str) -> FileResponse:
        # Source scope check before mutating — stops callers from renaming
        # something they only got a one-off share on.
        await self.get(file_id)
        return await asyncio.to_thread(self._rename_sync, file_id, new_name)

    @override
    async def move(self, file_id: str, new_parent_folder_id: str) -> MoveResult:
        source = await self.get(file_id)
        try:
            moved = await asyncio.to_thread(
                self._move_sync, file_id, new_parent_folder_id, source.parent_folder_id
            )
        except DriveUpstreamError as exc:
            # Drive returns 403 for both ordinary permission failures and
            # cross-shared-drive moves blocked by admin movement
            # restrictions. The latter is common enough — and confusing
            # enough — that we disambiguate here by fetching the
            # destination's drive_id. Only on a real cross-drive case do
            # we surface the typed CrossDriveMoveNotSupportedError; a
            # within-drive 403 keeps Drive's original message.
            if exc.http_status == 403:
                try:
                    destination = await self.get(new_parent_folder_id)
                except FileNotInScopeError:
                    raise exc from None
                if destination.drive_id != source.drive_id:
                    msg = "Drive blocked this cross-drive move — copy the file instead."
                    raise CrossDriveMoveNotSupportedError(msg) from exc
            raise
        # Destination scope check — reject moves *out* of Sarj Wiki's
        # visible surface (e.g. into My Drive).
        await self._ensure_in_member_drive(moved.drive_id)
        return MoveResult(source=source, moved=moved)

    @override
    async def copy(self, file_id: str, new_name: str) -> FileResponse:
        source = await self.get(file_id)
        if source.mime_type == MIME_FOLDER:
            raise FolderCopyNotSupportedError(
                "Folders can't be duplicated — copy individual files instead"
            )
        result = await asyncio.to_thread(self._copy_sync, file_id, new_name)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    @override
    async def delete(self, file_id: str) -> DeleteResult:
        source = await self.get(file_id)
        await asyncio.to_thread(self._trash_sync, file_id)
        return DeleteResult(source=source)

    # ------------------------------------------------------------------
    # Internals — sync Drive client wrapped by ``asyncio.to_thread`` so
    # the event loop stays free during the blocking Google API calls.
    # ------------------------------------------------------------------

    async def _ensure_in_member_drive(self, drive_id: str | None) -> None:
        if drive_id is None:
            raise FileNotInScopeError("File not available in Sarj Wiki")
        drives = await self._drive.list_drives()
        if not any(d.id == drive_id for d in drives):
            raise FileNotInScopeError("File not available in Sarj Wiki")

    def _create_sync(self, parent_folder_id: str, name: str, mime_type: str) -> FileResponse:
        client = self._client()
        body: File = {"name": name, "mimeType": mime_type, "parents": [parent_folder_id]}
        with _translated_drive_errors():
            raw = execute_mutation(
                client.files().create(body=body, fields=_FIELDS, supportsAllDrives=True)
            )
        return _to_response(raw)

    def _get_sync(self, file_id: str) -> FileResponse:
        client = self._client()
        with _translated_drive_errors():
            raw = execute_with_retry(
                client.files().get(fileId=file_id, fields=_FIELDS, supportsAllDrives=True)
            )
        return _to_response(raw)

    def _rename_sync(self, file_id: str, new_name: str) -> FileResponse:
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
        self, file_id: str, new_parent_folder_id: str, prev_parent_folder_id: str | None
    ) -> FileResponse:
        client = self._client()
        with _translated_drive_errors():
            if prev_parent_folder_id is None:
                meta = execute_with_retry(
                    client.files().get(fileId=file_id, fields="parents", supportsAllDrives=True)
                )
                prev_parent_folder_id = ",".join(meta.get("parents", []))
            raw = execute_with_retry(
                client.files().update(
                    fileId=file_id,
                    addParents=new_parent_folder_id,
                    removeParents=prev_parent_folder_id,
                    fields=_FIELDS,
                    supportsAllDrives=True,
                )
            )
        return _to_response(raw)

    def _copy_sync(self, file_id: str, new_name: str) -> FileResponse:
        client = self._client()
        body: File = {"name": new_name}
        with _translated_drive_errors():
            raw = execute_mutation(
                client.files().copy(
                    fileId=file_id, body=body, fields=_FIELDS, supportsAllDrives=True
                )
            )
        return _to_response(raw)

    def _trash_sync(self, file_id: str) -> None:
        client = self._client()
        body: File = {"trashed": True}
        with _translated_drive_errors():
            execute_with_retry(
                client.files().update(fileId=file_id, body=body, supportsAllDrives=True)
            )

    def _client(self) -> DriveResource:
        return GoogleDriveService.build_drive_client(self._user)


def _to_response(raw: File) -> FileResponse:
    file = GoogleFile.model_validate(raw)
    return FileResponse(
        id=file.id,
        name=file.name,
        mime_type=file.mime_type,
        parent_folder_id=file.parents[0] if file.parents else None,
        drive_id=file.drive_id,
        modified_time=file.modified_time,
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
