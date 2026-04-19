"""Drive file CRUD helpers, user-token only.

Every helper wraps one Drive API call inside ``asyncio.to_thread`` so
the caller's event loop stays free. HTTP 4xx from Drive raise
``HTTPException`` with a matched status; HTTP 5xx and transport errors
bubble as 502 so the caller doesn't silently succeed.

Strict-mode scoping
-------------------

Every file the Navigator reads or writes must live in a shared drive
the caller is a direct member of. A file visible to the user's token
via ``drive.files.get`` could also be (a) in the user's own My Drive
or (b) in a shared drive the user has one-off explicit access to but
isn't a member of. Both cases fall outside the "Navigator mirrors
shared drives" contract in PIVOT_PLAN.md §4.3 — leaving them allowed
lets anyone with a Drive file id deep-link through ``/d/{id}`` into
our chrome, which contradicts the sidebar's scoping. ``_ensure_in_
member_drive`` runs after every metadata fetch and raises 404 for
anything that isn't in a member shared drive.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from googleapiclient.errors import HttpError

from app.drive.credentials import get_drive_service
from app.drive.drives import list_user_drives
from app.drive.schemas import MIME_FOLDER, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.files.schemas import FileResponse
from app.models.user import User

if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.schemas import File

_FIELDS = "id,name,mimeType,parents,driveId,modifiedTime"


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


async def _ensure_in_member_drive(user: User, drive_id: str | None) -> None:
    """Reject files that aren't in a shared drive the user is a member of.

    Returns ``None`` on success; raises ``HTTPException(404)`` otherwise.
    We use 404 rather than 403 so the Navigator's error surface is
    indistinguishable from "file doesn't exist" — direct-URL probes for
    out-of-scope files shouldn't reveal whether they exist.
    """
    if drive_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not available in the Navigator",
        )
    drives = await list_user_drives(user)
    if not any(d.id == drive_id for d in drives):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not available in the Navigator",
        )


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
        body: File = {"name": name, "mimeType": mime_type, "parents": [parent_folder_id]}
        try:
            raw = (
                service.files().create(body=body, fields=_FIELDS, supportsAllDrives=True).execute()
            )
        except HttpError as e:
            _raise_from_http_error(e)
            raise  # unreachable but satisfies type checkers
        return _to_response(raw)

    result = await asyncio.to_thread(_sync)
    await _ensure_in_member_drive(user, result.drive_id)
    return result


async def rename_file(user: User, file_id: str, new_name: str) -> FileResponse:
    # Fetch metadata first so the strict-scope check runs before any
    # mutation — a caller with just a file id shouldn't be able to
    # rename something they only got a one-off share on.
    current = await get_file(user, file_id)
    _ = current  # keep the read for its scope side-effect only

    def _sync() -> FileResponse:
        service = get_drive_service(user)
        body: File = {"name": new_name}
        try:
            raw = (
                service.files()
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

    return await asyncio.to_thread(_sync)


async def move_file(
    user: User,
    file_id: str,
    new_parent_folder_id: str,
) -> FileResponse:
    # Source scope check — the file must currently live in a member
    # drive before we let the caller move it somewhere else.
    source = await get_file(user, file_id)
    _ = source

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

    result = await asyncio.to_thread(_sync)
    # Destination scope check — reject moves *out* of the Navigator's
    # visible surface (e.g. into My Drive or a non-member drive).
    await _ensure_in_member_drive(user, result.drive_id)
    return result


async def copy_file(user: User, file_id: str, new_name: str) -> FileResponse:
    """Duplicate ``file_id`` as a sibling with ``new_name``.

    Drive's ``files.copy`` endpoint handles every non-folder mime
    type — Docs, Sheets, Slides, Drawings, Forms, PDFs, images, and
    generic binary files all copy server-side in one call. Folders
    are explicitly rejected here with a 400 rather than surfacing
    Google's rather opaque 403 ``cannotCopyFile``; recursive folder
    copy would need a tree walk and isn't worth it for the feature.

    The copy inherits the source's parent folder automatically —
    Drive's default behavior — so "duplicate this" lands the new
    file as a sibling without any extra parent wiring on our side.
    Source scope is checked before the copy call via ``get_file``
    so a caller with just a file id can't clone an out-of-scope
    file. Destination scope is checked after the copy so the result
    still has to live in a member drive (true by default since the
    source did).
    """
    # Source scope check first so we never issue a Drive copy for a
    # file the caller shouldn't even be able to see.
    source = await get_file(user, file_id)
    if source.mime_type == MIME_FOLDER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Folders can't be duplicated — copy individual files instead",
        )

    def _sync() -> FileResponse:
        service = get_drive_service(user)
        body: File = {"name": new_name}
        try:
            raw = (
                service.files()
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

    result = await asyncio.to_thread(_sync)
    await _ensure_in_member_drive(user, result.drive_id)
    return result


async def trash_file(user: User, file_id: str) -> None:
    source = await get_file(user, file_id)
    _ = source

    def _sync() -> None:
        service = get_drive_service(user)
        body: File = {"trashed": True}
        try:
            service.files().update(
                fileId=file_id,
                body=body,
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

    result = await asyncio.to_thread(_sync)
    await _ensure_in_member_drive(user, result.drive_id)
    return result
