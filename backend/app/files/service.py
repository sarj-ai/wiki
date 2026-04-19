"""File CRUD service.

Everything here uses the caller's own OAuth token — no service account.
HTTP 4xx from Drive is mapped to ``HTTPException`` with a matching
status; 5xx and transport errors bubble as 502.

Strict-mode scoping
-------------------

Every file the Navigator reads or writes must live in a shared drive
the caller is a direct member of. A file the caller's token can see via
``files.get`` could also be in their personal My Drive or in a shared
drive they have one-off share access to but aren't a member of — both
fall outside the sidebar's visible surface. ``_ensure_in_member_drive``
runs after every metadata fetch and raises 404 (not 403, to avoid
leaking existence) for anything outside a member drive.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from googleapiclient.errors import HttpError

from app.drive.credentials import build_drive_client
from app.drive.schemas import MIME_FOLDER, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.drive.service import DriveService
from app.files.schemas import FileResponse
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
    async def move(self, file_id: str, new_parent_folder_id: str) -> FileResponse: ...

    @abstractmethod
    async def copy(self, file_id: str, new_name: str) -> FileResponse: ...

    @abstractmethod
    async def trash(self, file_id: str) -> None: ...


class GoogleFilesService(FilesService):
    """``FilesService`` backed by the real Drive v3 API."""

    def __init__(self, *, user: User, drive: DriveService) -> None:
        self._user = user
        self._drive = drive

    async def create(self, parent_folder_id: str, name: str, mime_type: str) -> FileResponse:
        if mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported mimeType: {mime_type}",
            )
        result = await asyncio.to_thread(self._create_sync, parent_folder_id, name, mime_type)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    async def get(self, file_id: str) -> FileResponse:
        result = await asyncio.to_thread(self._get_sync, file_id)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    async def rename(self, file_id: str, new_name: str) -> FileResponse:
        # Source scope check before mutating — stops callers from renaming
        # something they only got a one-off share on.
        await self.get(file_id)
        return await asyncio.to_thread(self._rename_sync, file_id, new_name)

    async def move(self, file_id: str, new_parent_folder_id: str) -> FileResponse:
        await self.get(file_id)
        result = await asyncio.to_thread(self._move_sync, file_id, new_parent_folder_id)
        # Destination scope check — reject moves *out* of the Navigator's
        # visible surface (e.g. into My Drive).
        await self._ensure_in_member_drive(result.drive_id)
        return result

    async def copy(self, file_id: str, new_name: str) -> FileResponse:
        source = await self.get(file_id)
        if source.mime_type == MIME_FOLDER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Folders can't be duplicated — copy individual files instead",
            )
        result = await asyncio.to_thread(self._copy_sync, file_id, new_name)
        await self._ensure_in_member_drive(result.drive_id)
        return result

    async def trash(self, file_id: str) -> None:
        await self.get(file_id)
        await asyncio.to_thread(self._trash_sync, file_id)

    # ------------------------------------------------------------------
    # Internals — sync Drive client wrapped by ``asyncio.to_thread`` so
    # the event loop stays free during the blocking Google API calls.
    # ------------------------------------------------------------------

    async def _ensure_in_member_drive(self, drive_id: str | None) -> None:
        if drive_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not available in the Navigator",
            )
        drives = await self._drive.list_drives()
        if not any(d.id == drive_id for d in drives):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not available in the Navigator",
            )

    def _create_sync(self, parent_folder_id: str, name: str, mime_type: str) -> FileResponse:
        client = self._client()
        body: File = {"name": name, "mimeType": mime_type, "parents": [parent_folder_id]}
        try:
            raw = client.files().create(body=body, fields=_FIELDS, supportsAllDrives=True).execute()
        except HttpError as e:
            _raise_from_http_error(e)
            raise  # unreachable — _raise_from_http_error always raises
        return _to_response(raw)

    def _get_sync(self, file_id: str) -> FileResponse:
        client = self._client()
        try:
            raw = (
                client.files().get(fileId=file_id, fields=_FIELDS, supportsAllDrives=True).execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise
        return _to_response(raw)

    def _rename_sync(self, file_id: str, new_name: str) -> FileResponse:
        client = self._client()
        body: File = {"name": new_name}
        try:
            raw = (
                client.files()
                .update(
                    fileId=file_id,
                    body=body,
                    fields=_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise
        return _to_response(raw)

    def _move_sync(self, file_id: str, new_parent_folder_id: str) -> FileResponse:
        client = self._client()
        try:
            meta = (
                client.files()
                .get(fileId=file_id, fields="parents", supportsAllDrives=True)
                .execute()
            )
            prev_parents = ",".join(meta.get("parents", []))
            raw = (
                client.files()
                .update(
                    fileId=file_id,
                    addParents=new_parent_folder_id,
                    removeParents=prev_parents,
                    fields=_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise
        return _to_response(raw)

    def _copy_sync(self, file_id: str, new_name: str) -> FileResponse:
        client = self._client()
        body: File = {"name": new_name}
        try:
            raw = (
                client.files()
                .copy(
                    fileId=file_id,
                    body=body,
                    fields=_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise
        return _to_response(raw)

    def _trash_sync(self, file_id: str) -> None:
        client = self._client()
        body: File = {"trashed": True}
        try:
            client.files().update(
                fileId=file_id,
                body=body,
                supportsAllDrives=True,
            ).execute()
        except HttpError as e:
            _raise_from_http_error(e)
            raise

    def _client(self) -> DriveResource:
        return build_drive_client(self._user)


def _to_response(raw: File) -> FileResponse:
    parents = raw.get("parents") or []
    return FileResponse(
        id=raw["id"],
        name=raw["name"],
        mime_type=raw["mimeType"],
        parent_folder_id=parents[0] if parents else None,
        drive_id=raw.get("driveId"),
        modified_time=raw.get("modifiedTime"),
    )


def _raise_from_http_error(exc: HttpError) -> None:
    # Drive's JSON error body is informative; the status is what we
    # surface to the caller.
    code = exc.resp.status if exc.resp else 502
    mapped = {
        400: status.HTTP_400_BAD_REQUEST,
        401: status.HTTP_401_UNAUTHORIZED,
        403: status.HTTP_403_FORBIDDEN,
        404: status.HTTP_404_NOT_FOUND,
    }.get(code, status.HTTP_502_BAD_GATEWAY)
    detail = str(exc) or "Drive request failed"
    raise HTTPException(status_code=mapped, detail=detail)
