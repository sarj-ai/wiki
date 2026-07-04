from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast, override

from googleapiclient.errors import HttpError
from loguru import logger

from app.drive.google_schemas import GoogleFile
from app.drive.retry import execute_mutation, execute_with_retry
from app.drive.schemas import MIME_DOC, MIME_FOLDER, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.drive.service import DriveService, GoogleDriveService, reuse_drive_client
from app.files.errors import (
    CrossDriveMoveNotSupportedError,
    DriveUpstreamError,
    FileNotInScopeError,
    FolderCopyNotSupportedError,
    UnsupportedMimeError,
)
from app.files.schemas import (
    CommentReply,
    DeleteResult,
    DocComment,
    DocHtml,
    DocTab,
    FileResponse,
    MoveResult,
)
from app.models.user import User


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource
    from googleapiclient._apis.drive.v3.schemas import File

_FIELDS = "id,name,mimeType,parents,driveId,modifiedTime"


class FilesService(ABC):
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

    @abstractmethod
    async def export_doc(self, file_id: str) -> DocHtml: ...

    @abstractmethod
    async def list_comments(self, file_id: str) -> list[DocComment]: ...


class GoogleFilesService(FilesService):
    def __init__(self, *, user: User, drive: DriveService) -> None:
        self._user = user
        self._drive = drive
        self._clients: dict[int, DriveResource] = {}

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

    @override
    async def export_doc(self, file_id: str) -> DocHtml:
        file = await self.get(file_id)  # one scope check covers both fetches below
        if file.mime_type != MIME_DOC:
            raise UnsupportedMimeError("Only Google Docs can be exported to HTML")
        html, tabs = await asyncio.gather(
            asyncio.to_thread(self._export_html_sync, file_id),
            asyncio.to_thread(self._list_tabs_sync, file_id),
        )
        return DocHtml(html=html, tabs=tabs)

    @override
    async def list_comments(self, file_id: str) -> list[DocComment]:
        await self.get(file_id)  # enforces drive membership
        return await asyncio.to_thread(self._list_comments_sync, file_id)

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

    def _export_html_sync(self, file_id: str) -> str:
        # HTML export embeds images as base64 data URIs, so the blob is self-contained.
        client = self._client()
        with _translated_drive_errors():
            raw = execute_with_retry(client.files().export(fileId=file_id, mimeType="text/html"))
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    def _list_tabs_sync(self, file_id: str) -> list[DocTab]:
        client = GoogleDriveService.build_docs_client(self._user)
        try:
            raw = execute_with_retry(
                client.documents().get(documentId=file_id, includeTabsContent=False, fields="tabs")
            )
        except HttpError:
            # Docs API disabled or doc predates tabs — the view falls back to a flat list.
            logger.warning("doc tabs fetch failed file={}", file_id)
            return []
        tabs: list[DocTab] = []
        _walk_tabs(raw.get("tabs", []), 0, tabs)
        return tabs

    def _list_comments_sync(self, file_id: str) -> list[DocComment]:
        client = self._client()
        fields = (
            "comments(id,content,author(displayName),quotedFileContent(value),"
            "resolved,createdTime,replies(content,author(displayName),createdTime))"
        )
        with _translated_drive_errors():
            # ponytail: first 100 comments only; add paging if a doc ever exceeds that
            raw = execute_with_retry(
                client.comments().list(fileId=file_id, fields=fields, pageSize=100)
            )
        return [_to_comment(c) for c in raw.get("comments", [])]

    def _client(self) -> DriveResource:
        return reuse_drive_client(self._user, self._clients)


def _as_dict(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _walk_tabs(nodes: list[object], level: int, out: list[DocTab]) -> None:
    for node in nodes:
        tab = _as_dict(node)
        title = _as_dict(tab.get("tabProperties")).get("title")
        out.append(DocTab(title=str(title or "Untitled"), level=level))
        children = tab.get("childTabs")
        _walk_tabs(
            cast("list[object]", children) if isinstance(children, list) else [], level + 1, out
        )


def _to_comment(raw: dict[str, object]) -> DocComment:
    replies = raw.get("replies")
    return DocComment(
        id=str(raw.get("id", "")),
        author=_author_of(raw),
        content=str(raw.get("content", "")),
        quoted_text=_opt_str(_as_dict(raw.get("quotedFileContent")).get("value")),
        resolved=bool(raw.get("resolved", False)),
        created_time=_opt_str(raw.get("createdTime")),
        replies=[_to_reply(_as_dict(r)) for r in (replies if isinstance(replies, list) else [])],
    )


def _to_reply(raw: dict[str, object]) -> CommentReply:
    return CommentReply(
        author=_author_of(raw),
        content=str(raw.get("content", "")),
        created_time=_opt_str(raw.get("createdTime")),
    )


def _author_of(node: dict[str, object]) -> str:
    name = _as_dict(node.get("author")).get("displayName")
    return name if isinstance(name, str) else "Unknown"


def _opt_str(value: object) -> str | None:
    return str(value) if value is not None else None


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
