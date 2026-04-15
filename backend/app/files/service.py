"""Drive file CRUD helpers, user-token only.

Each helper wraps one Drive API call inside ``asyncio.to_thread`` so
the caller's event loop stays free. HTTP 4xx from Drive raise
``HTTPException`` with a matched status; HTTP 5xx and transport
errors bubble as 502 so the caller doesn't silently succeed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException, status
from googleapiclient.errors import HttpError

from app.drive.credentials import get_drive_service
from app.drive.schemas import NAVIGATOR_SUPPORTED_MIME_TYPES
from app.files.schemas import FileResponse
from app.models.user import User

log = logging.getLogger(__name__)

_FIELDS = "id,name,mimeType,parents,driveId,modifiedTime"


def _to_response(raw: dict[str, Any]) -> FileResponse:
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
    """Map Drive HttpError to FastAPI HTTPException.

    Drive's JSON error body is informative; the status is what we
    actually want to surface to the caller.
    """
    code = exc.resp.status if exc.resp else 502
    mapped = {
        400: status.HTTP_400_BAD_REQUEST,
        401: status.HTTP_401_UNAUTHORIZED,
        403: status.HTTP_403_FORBIDDEN,
        404: status.HTTP_404_NOT_FOUND,
    }.get(code, status.HTTP_502_BAD_GATEWAY)
    detail = str(exc) or "Drive request failed"
    raise HTTPException(status_code=mapped, detail=detail)


async def create_file(
    user: User,
    parent_folder_id: str,
    name: str,
    mime_type: str,
) -> FileResponse:
    if mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported mimeType: {mime_type}",
        )

    def _sync() -> FileResponse:
        service = get_drive_service(user)
        body = {"name": name, "mimeType": mime_type, "parents": [parent_folder_id]}
        try:
            raw = (
                service.files()
                .create(body=body, fields=_FIELDS, supportsAllDrives=True)
                .execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise  # unreachable but satisfies type checkers
        return _to_response(raw)

    return await asyncio.to_thread(_sync)


async def rename_file(user: User, file_id: str, new_name: str) -> FileResponse:
    def _sync() -> FileResponse:
        service = get_drive_service(user)
        try:
            raw = (
                service.files()
                .update(
                    fileId=file_id,
                    body={"name": new_name},
                    fields=_FIELDS,
                    supportsAllDrives=True,
                )
                .execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise
        return _to_response(raw)

    return await asyncio.to_thread(_sync)


async def move_file(
    user: User,
    file_id: str,
    new_parent_folder_id: str,
) -> FileResponse:
    def _sync() -> FileResponse:
        service = get_drive_service(user)
        try:
            meta = (
                service.files()
                .get(fileId=file_id, fields="parents", supportsAllDrives=True)
                .execute()
            )
            prev_parents = ",".join(meta.get("parents", []))
            raw = (
                service.files()
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

    return await asyncio.to_thread(_sync)


async def trash_file(user: User, file_id: str) -> None:
    def _sync() -> None:
        service = get_drive_service(user)
        try:
            service.files().update(
                fileId=file_id,
                body={"trashed": True},
                supportsAllDrives=True,
            ).execute()
        except HttpError as e:
            _raise_from_http_error(e)
            raise

    await asyncio.to_thread(_sync)


async def get_file(user: User, file_id: str) -> FileResponse:
    def _sync() -> FileResponse:
        service = get_drive_service(user)
        try:
            raw = (
                service.files()
                .get(fileId=file_id, fields=_FIELDS, supportsAllDrives=True)
                .execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise
        return _to_response(raw)

    return await asyncio.to_thread(_sync)
