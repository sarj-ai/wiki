from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import batched
from typing import TYPE_CHECKING, Any, override

from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field, ValidationError

from app.drive.google_schemas import GoogleFile
from app.drive.retry import execute_batch_with_retry, execute_mutation, execute_with_retry
from app.drive.schemas import MIME_FOLDER, MIME_HTML, NAVIGATOR_SUPPORTED_MIME_TYPES
from app.drive.service import DriveService, reuse_drive_client
from app.files.errors import (
    CrossDriveMoveNotSupportedError,
    DriveUpstreamError,
    FileNotInScopeError,
    FilesError,
    FileVersionChangedError,
    FolderCopyNotSupportedError,
    UnsupportedMimeError,
)
from app.files.schemas import DeleteResult, FileResponse, MoveResult
from app.identifiers import DriveId, FileId
from app.models.user import User


if TYPE_CHECKING:
    from googleapiclient._apis.drive.v3.resources import DriveResource
    from googleapiclient._apis.drive.v3.schemas import File

_FIELDS = (
    "id,name,mimeType,parents,driveId,modifiedTime,description,trashed,"
    "capabilities(canEdit,canAddChildren),appProperties"
)
_SMART_TASK_PROPERTY = "sarjWikiTaskId"
_MAX_HTML_BYTES = 5 * 1024 * 1024
_FILE_GET_BATCH_SIZE = 100


class _DriveErrorItem(BaseModel):
    reason: str | None = None


class _DriveErrorBody(BaseModel):
    errors: list[_DriveErrorItem] = Field(default_factory=list)


class _DriveErrorPayload(BaseModel):
    error: _DriveErrorBody = Field(default_factory=_DriveErrorBody)


@dataclass(frozen=True)
class _AccessibleFile:
    id: FileId
    response: FileResponse


class FilesService(ABC):
    @abstractmethod
    async def create(self, parent_folder_id: FileId, name: str, mime_type: str) -> FileResponse: ...

    async def create_tracked(
        self,
        parent_folder_id: FileId,
        name: str,
        mime_type: str,
        task_id: str,
    ) -> FileResponse:
        raise NotImplementedError

    async def copy_template(
        self,
        *,
        template_file_id: FileId,
        parent_folder_id: FileId,
        name: str,
        task_id: str,
        expected_modified_time: str,
    ) -> FileResponse:
        raise NotImplementedError

    async def find_by_task(self, drive_id: DriveId, task_id: str) -> FileResponse | None:
        raise NotImplementedError

    @abstractmethod
    async def get(self, file_id: FileId) -> FileResponse: ...

    async def get_accessible_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        async def load(file_id: FileId) -> _AccessibleFile | None:
            try:
                return _AccessibleFile(file_id, await self.get(file_id))
            except FilesError as exc:
                if exc.http_status in {403, 404}:
                    return None
                raise

        loaded = await asyncio.gather(*(load(file_id) for file_id in dict.fromkeys(file_ids)))
        return {item.id: item.response for item in loaded if item is not None}

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
        return await self._normalize_member_drive(result)

    @override
    async def create_tracked(
        self,
        parent_folder_id: FileId,
        name: str,
        mime_type: str,
        task_id: str,
    ) -> FileResponse:
        if mime_type not in NAVIGATOR_SUPPORTED_MIME_TYPES:
            msg = f"Unsupported mimeType: {mime_type}"
            raise UnsupportedMimeError(msg)
        destination = await self.get(parent_folder_id)
        if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
            msg = "Destination does not accept new files"
            raise FileNotInScopeError(msg)
        result = await asyncio.to_thread(
            self._create_sync,
            parent_folder_id,
            name,
            mime_type,
            task_id,
        )
        return await self._normalize_member_drive(result)

    @override
    async def copy_template(
        self,
        *,
        template_file_id: FileId,
        parent_folder_id: FileId,
        name: str,
        task_id: str,
        expected_modified_time: str,
    ) -> FileResponse:
        source, destination = await asyncio.gather(
            self.get(template_file_id),
            self.get(parent_folder_id),
        )
        if source.mime_type == MIME_FOLDER:
            msg = "Folders can't be used as templates"
            raise FolderCopyNotSupportedError(msg)
        if source.modified_time != expected_modified_time:
            raise FileVersionChangedError
        if destination.mime_type != MIME_FOLDER or not destination.can_add_children:
            msg = "Destination does not accept new files"
            raise FileNotInScopeError(msg)
        if source.drive_id != destination.drive_id:
            msg = "Templates must be copied within their workspace"
            raise FileNotInScopeError(msg)
        result = await asyncio.to_thread(
            self._copy_template_sync,
            template_file_id=template_file_id,
            parent_folder_id=parent_folder_id,
            new_name=name,
            task_id=task_id,
        )
        return await self._normalize_member_drive(result)

    @override
    async def find_by_task(self, drive_id: DriveId, task_id: str) -> FileResponse | None:
        drives = await self._drive.list_drives()
        is_personal = any(drive.id == drive_id and drive.is_personal for drive in drives)
        result = await asyncio.to_thread(
            self._find_by_task_sync,
            drive_id,
            task_id,
            is_personal=is_personal,
        )
        return await self._normalize_member_drive(result) if result is not None else None

    @override
    async def get(self, file_id: FileId) -> FileResponse:
        result = await asyncio.to_thread(self._get_sync, file_id)
        return await self._normalize_member_drive(result)

    @override
    async def get_accessible_many(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        unique_ids = list(dict.fromkeys(file_ids))
        if not unique_ids:
            return {}
        fetched = await asyncio.to_thread(self._get_accessible_many_sync, unique_ids)

        async def normalize(file: FileResponse) -> _AccessibleFile | None:
            try:
                return _AccessibleFile(file.id, await self._normalize_member_drive(file))
            except FilesError as exc:
                if exc.http_status in {403, 404}:
                    return None
                raise

        normalized = await asyncio.gather(*(normalize(file) for file in fetched.values()))
        return {item.id: item.response for item in normalized if item is not None}

    @override
    async def can_edit(self, file_id: FileId) -> bool:
        result = await asyncio.to_thread(self._get_sync, file_id)
        await self._normalize_member_drive(result)
        return result.can_edit

    @override
    async def get_html_content(self, file_id: FileId) -> bytes:
        file = await self.get(file_id)
        if file.mime_type != MIME_HTML:
            msg = "Only HTML files can use the isolated HTML viewer"
            raise UnsupportedMimeError(msg)
        drives = await self._drive.list_drives()
        is_personal = any(d.id == file.drive_id and d.is_personal for d in drives)
        if not is_personal:
            await asyncio.to_thread(self._ensure_current_drive_membership_sync, file.drive_id)
        content = await asyncio.to_thread(self._download_sync, file_id)
        if len(content) > _MAX_HTML_BYTES:
            msg = "HTML file exceeds the 5 MiB viewer limit"
            raise UnsupportedMimeError(msg)
        return content

    @override
    async def rename(self, file_id: FileId, new_name: str) -> FileResponse:
        await self.get(file_id)
        result = await asyncio.to_thread(self._rename_sync, file_id=file_id, new_name=new_name)
        return await self._normalize_member_drive(result)

    @override
    async def move(self, file_id: FileId, new_parent_folder_id: FileId) -> MoveResult:
        source, destination = await asyncio.gather(
            self.get(file_id),
            self.get(new_parent_folder_id),
        )
        if destination.mime_type != MIME_FOLDER:
            msg = "Move destination must be a folder"
            raise UnsupportedMimeError(msg)
        if not destination.can_add_children:
            raise DriveUpstreamError(403)
        try:
            moved = await asyncio.to_thread(
                self._move_sync,
                file_id=file_id,
                new_parent_folder_id=new_parent_folder_id,
                prev_parent_folder_id=source.parent_folder_id,
            )
        except DriveUpstreamError as exc:
            if (
                destination.drive_id != source.drive_id
                and source.mime_type == MIME_FOLDER
                and exc.reason == "teamDrivesFolderMoveInNotSupported"
            ):
                msg = "Google Drive cannot move this folder between these drives"
                raise CrossDriveMoveNotSupportedError(msg) from exc
            raise
        moved = await self._normalize_member_drive(moved)
        return MoveResult(source=source, moved=moved)

    @override
    async def copy(self, file_id: FileId, new_name: str) -> FileResponse:
        source = await self.get(file_id)
        if source.mime_type == MIME_FOLDER:
            msg = "Folders can't be duplicated — copy individual files instead"
            raise FolderCopyNotSupportedError(msg)
        result = await asyncio.to_thread(self._copy_sync, file_id=file_id, new_name=new_name)
        return await self._normalize_member_drive(result)

    @override
    async def delete(self, file_id: FileId) -> DeleteResult:
        source = await self.get(file_id)
        await asyncio.to_thread(self._trash_sync, file_id)
        return DeleteResult(source=source)

    async def _normalize_member_drive(self, file: FileResponse) -> FileResponse:
        resolved_drive_id = await self._drive.resolve_member_drive(file.id, file.drive_id)
        if resolved_drive_id is None:
            msg = "File not available in Sarj Wiki"
            raise FileNotInScopeError(msg)
        if resolved_drive_id == file.drive_id:
            return file
        return file.model_copy(update={"drive_id": resolved_drive_id})

    def _create_sync(
        self,
        parent_folder_id: FileId,
        name: str,
        mime_type: str,
        task_id: str | None = None,
    ) -> FileResponse:
        client = self._client()
        body: File = {"name": name, "mimeType": mime_type, "parents": [parent_folder_id]}
        if task_id is not None:
            body["appProperties"] = {_SMART_TASK_PROPERTY: task_id}
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
        return _to_accessible_response(raw)

    def _get_accessible_many_sync(self, file_ids: list[FileId]) -> dict[FileId, FileResponse]:
        client = self._client()
        responses: dict[FileId, FileResponse] = {}
        failures: list[BaseException] = []

        def collect(request_id: str, response: Any, exception: BaseException | None) -> None:
            if exception is not None:
                status = getattr(getattr(exception, "resp", None), "status", None)
                if status not in {403, 404}:
                    failures.append(exception)
                return
            file = GoogleFile.model_validate(response)
            if file.trashed:
                return
            responses[FileId(request_id)] = _file_response(file)

        with _translated_drive_errors():
            for chunk in batched(file_ids, _FILE_GET_BATCH_SIZE, strict=False):
                batch = client.new_batch_http_request(callback=collect)
                for file_id in chunk:
                    batch.add(
                        client.files().get(
                            fileId=file_id,
                            fields=_FIELDS,
                            supportsAllDrives=True,
                        ),
                        request_id=file_id,
                    )
                execute_batch_with_retry(batch)
        if failures:
            failure = failures[0]
            if isinstance(failure, HttpError):
                raise _drive_upstream(failure) from None
            raise DriveUpstreamError(502) from None
        return responses

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
        body: File = {}
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
                    body=body,
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

    def _copy_template_sync(
        self,
        *,
        template_file_id: FileId,
        parent_folder_id: FileId,
        new_name: str,
        task_id: str,
    ) -> FileResponse:
        client = self._client()
        body: File = {
            "name": new_name,
            "parents": [parent_folder_id],
            "appProperties": {_SMART_TASK_PROPERTY: task_id},
        }
        with _translated_drive_errors():
            raw = execute_mutation(
                client.files().copy(
                    fileId=template_file_id,
                    body=body,
                    fields=_FIELDS,
                    supportsAllDrives=True,
                )
            )
        return _to_response(raw)

    def _find_by_task_sync(
        self,
        drive_id: DriveId,
        task_id: str,
        *,
        is_personal: bool,
    ) -> FileResponse | None:
        client = self._client()
        query = (
            f"appProperties has {{ key='{_SMART_TASK_PROPERTY}' and value='{task_id}' }} "
            "and trashed = false"
        )
        with _translated_drive_errors():
            request = (
                client.files().list(
                    corpora="user",
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    q=query,
                    fields=f"files({_FIELDS})",
                    pageSize=2,
                )
                if is_personal
                else client.files().list(
                    corpora="drive",
                    driveId=drive_id,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    q=query,
                    fields=f"files({_FIELDS})",
                    pageSize=2,
                )
            )
            raw = execute_with_retry(request)
        files = raw.get("files", [])
        if is_personal:
            files = [file for file in files if file.get("driveId") is None]
        return _to_response(files[0]) if files else None

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
            raise DriveUpstreamError(502)
        return raw

    def _client(self) -> DriveResource:
        return reuse_drive_client(self._user, self._clients)


def _to_response(raw: File) -> FileResponse:
    return _file_response(GoogleFile.model_validate(raw))


def _to_accessible_response(raw: File) -> FileResponse:
    file = GoogleFile.model_validate(raw)
    if file.trashed:
        msg = "File not available in Sarj Wiki"
        raise FileNotInScopeError(msg)
    return _file_response(file)


def _file_response(file: GoogleFile) -> FileResponse:
    return FileResponse(
        id=file.id,
        name=file.name,
        mime_type=file.mime_type,
        parent_folder_id=file.parents[0] if file.parents else None,
        drive_id=file.drive_id,
        modified_time=file.modified_time,
        can_edit=file.capabilities.can_edit,
        can_add_children=file.capabilities.can_add_children,
        description=file.description,
    )


@contextmanager
def _translated_drive_errors() -> Generator[None]:
    try:
        yield
    except HttpError as e:
        raise _drive_upstream(e) from None


def _drive_upstream(exc: HttpError) -> DriveUpstreamError:
    code = exc.resp.status if exc.resp else 502
    try:
        payload = _DriveErrorPayload.model_validate_json(exc.content)
        reason = next(
            (item.reason for item in payload.error.errors if item.reason is not None), None
        )
    except ValidationError:
        reason = None
    return DriveUpstreamError(code, reason=reason)
