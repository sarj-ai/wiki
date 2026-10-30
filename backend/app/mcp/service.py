from __future__ import annotations

from typing import Protocol, override
from urllib.parse import quote

from app.config import settings
from app.document_transform.schemas import DocumentTransformResult
from app.files.errors import FileNotInScopeError
from app.files.schemas import FileResponse
from app.identifiers import UserId
from app.mcp.context import McpApplicationContext
from app.mcp.schemas import (
    MAX_EXCERPT_CHARACTERS,
    MAX_RESPONSE_BYTES,
    AdmissionInput,
    AdmissionResponse,
    ConnectionResponse,
    DocumentExcerpt,
    DocumentInput,
    DocumentResponse,
    McpCapability,
    SearchInput,
    SearchResponse,
    SearchResult,
    ShortLinkInput,
    ShortLinkResponse,
    TaskInput,
    TaskResponse,
)
from app.tasks.errors import TaskNotFoundError
from app.tasks.schemas import FailedTaskRead, PendingTaskRead, RunningTaskRead, TaskKind


class McpReader(Protocol):
    def connection(self) -> ConnectionResponse: ...

    async def search(self, request: SearchInput) -> SearchResponse: ...

    async def get_document(self, request: DocumentInput) -> DocumentResponse: ...

    async def get_admission(self, request: AdmissionInput) -> AdmissionResponse: ...

    async def get_task(self, request: TaskInput) -> TaskResponse: ...

    async def get_short_link(self, request: ShortLinkInput) -> ShortLinkResponse: ...


class McpReadService(McpReader):
    def __init__(self, context: McpApplicationContext) -> None:
        self._context = context

    @override
    def connection(self) -> ConnectionResponse:
        self._require_read()
        return ConnectionResponse(capabilities=sorted(self._context.principal.capabilities))

    @override
    async def search(self, request: SearchInput) -> SearchResponse:
        self._require_read()
        async with self._context.drive.search_bounded(request.query, limit=request.limit) as found:
            hits = found.files
            selected = list(dict.fromkeys(hit.id for hit in hits))[: request.limit]
            accessible = await self._context.files.get_search_metadata_many(selected)
        results: list[SearchResult] = []
        used_bytes = 1024
        for file_id in selected:
            file = accessible.get(file_id)
            if file is None or file.id != file_id:
                continue
            item = self._document(file)
            used_bytes += len(item.model_dump_json().encode("utf-8")) + 1
            if used_bytes > MAX_RESPONSE_BYTES:
                break
            results.append(item)
        return SearchResponse(
            results=results,
            returned_count=len(results),
            truncated=found.truncated or len(hits) > len(results),
        )

    @override
    async def get_document(self, request: DocumentInput) -> DocumentResponse:
        self._require_read()
        file = await self._context.files.get(request.file_id)
        if file.id != request.file_id:
            message = "File not available in Sarj Wiki"
            raise FileNotInScopeError(message)
        excerpt = None
        if request.include_excerpt:
            text = await self._context.drive.fetch_document_text(file.id)
            if text is not None:
                excerpt = DocumentExcerpt(text=text[:MAX_EXCERPT_CHARACTERS])
        return DocumentResponse(
            document=self._document(file), can_edit=file.can_edit, excerpt=excerpt
        )

    @override
    async def get_admission(self, request: AdmissionInput) -> AdmissionResponse:
        self._require_read()
        admission = await self._context.tasks.get_document_transform_admission(
            self._context.principal.user_id, request.idempotency_key
        )
        return AdmissionResponse(admission=admission)

    @override
    async def get_task(self, request: TaskInput) -> TaskResponse:
        self._require_read()
        task = await self._context.tasks.get_for_owner(
            request.task_id, UserId(self._context.principal.user_id)
        )
        if task.kind is not TaskKind.FILE_DOCUMENT_TRANSFORM:
            raise TaskNotFoundError
        if isinstance(task.result, DocumentTransformResult):
            output = task.result.output
            file = await self._context.files.get(output.file_id)
            if file.id != output.file_id:
                raise TaskNotFoundError
            result = task.result.model_copy(
                update={"output": output.model_copy(update={"web_url": self._url("d", file.id)})}
            )
            task = task.model_copy(update={"result": result, "resource_id": None})
        elif isinstance(task, FailedTaskRead):
            task = task.model_copy(
                update={
                    "resource_id": None,
                    "unverified_output": None,
                    "error_detail": "Document transformation failed; review the task in Wiki.",
                }
            )
        elif isinstance(task, RunningTaskRead):
            task = task.model_copy(update={"resource_id": None})
        return TaskResponse(
            task=task,
            retry_after_seconds=1 if isinstance(task, PendingTaskRead | RunningTaskRead) else None,
        )

    @override
    async def get_short_link(self, request: ShortLinkInput) -> ShortLinkResponse:
        self._require_read()
        alias = (await self._context.aliases.get_for_file(request.file_id)).alias
        return ShortLinkResponse(
            file_id=request.file_id,
            alias=alias,
            wiki_url=self._url("go", alias) if alias is not None else None,
        )

    def _require_read(self) -> None:
        self._context.principal.require(McpCapability.READ)

    @staticmethod
    def _url(route: str, identifier: str) -> str:
        return f"{settings.frontend_origin}/{route}/{quote(identifier, safe='')}"

    @classmethod
    def _document(cls, file: FileResponse) -> SearchResult:
        return SearchResult(
            file_id=file.id,
            name=file.name[:1024],
            mime_type=file.mime_type,
            modified_time=file.modified_time,
            wiki_url=cls._url("d", file.id),
        )
