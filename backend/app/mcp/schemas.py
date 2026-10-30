from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.aliases.schemas import DocumentAliasValue
from app.document_transform.schemas import (
    DocumentTransformCapabilities,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    MarkdownContent,
    MarkdownSource,
    OutputName,
    OutputStrategy,
    TransformMode,
)
from app.identifiers import FileId, IdempotencyKey, TaskId
from app.tasks.schemas import DocumentTransformAdmission, TaskKind, TaskRead


MAX_RESPONSE_BYTES = 65_536
MAX_SEARCH_RESULTS = 50
MAX_EXCERPT_CHARACTERS = 12_000
McpFileId = Annotated[
    FileId, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]
Freshness = Annotated[str, StringConstraints(min_length=1, max_length=128)]
WikiUrl = Annotated[str, StringConstraints(min_length=1, max_length=2048)]


class McpCapability(StrEnum):
    READ = "wiki:read"
    TRANSFORM = "wiki:transform"


class McpModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class McpResponse(McpModel):
    @model_validator(mode="after")
    def bound_serialized_response(self) -> Self:
        if len(self.model_dump_json().encode("utf-8")) > MAX_RESPONSE_BYTES:
            msg = "MCP response exceeds the bounded response budget"
            raise ValueError(msg)
        return self


class ConnectionInput(McpModel):
    pass


class ConnectionResponse(McpResponse):
    connected: Literal[True] = True
    capabilities: list[McpCapability] = Field(min_length=1, max_length=2)
    transform_capabilities: DocumentTransformCapabilities | None = None

    @model_validator(mode="after")
    def validate_grants(self) -> Self:
        grants = set(self.capabilities)
        if McpCapability.READ not in grants or len(grants) != len(self.capabilities):
            msg = "Connection requires a unique read grant"
            raise ValueError(msg)
        if self.transform_capabilities is not None and McpCapability.TRANSFORM not in grants:
            msg = "Transform capabilities require an explicit transform grant"
            raise ValueError(msg)
        return self


class SearchInput(McpModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]
    limit: int = Field(default=20, ge=1, le=MAX_SEARCH_RESULTS, strict=True)


class SearchResult(McpModel):
    file_id: McpFileId
    name: Annotated[str, StringConstraints(max_length=1024)]
    mime_type: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    modified_time: Freshness | None
    wiki_url: WikiUrl


class SearchResponse(McpResponse):
    results: list[SearchResult] = Field(max_length=MAX_SEARCH_RESULTS)
    coverage: Literal["bounded_search"] = "bounded_search"
    complete: Literal[False] = False
    content_trust: Literal["untrusted_document_data"] = "untrusted_document_data"
    returned_count: int = Field(ge=0, le=MAX_SEARCH_RESULTS, strict=True)
    truncated: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_count(self) -> Self:
        if self.returned_count != len(self.results):
            msg = "returned_count must equal the returned result count"
            raise ValueError(msg)
        return self


class DocumentInput(McpModel):
    file_id: McpFileId
    include_excerpt: bool = Field(default=False, strict=True)


class DocumentExcerpt(McpModel):
    text: Annotated[str, StringConstraints(max_length=MAX_EXCERPT_CHARACTERS)]
    content_kind: Literal["excerpt"] = "excerpt"
    complete: Literal[False] = False
    structure_preserved: Literal[False] = False
    content_trust: Literal["untrusted_document_data"] = "untrusted_document_data"
    warning: Literal[
        "Lossy excerpt: text may be omitted; tabs, formatting, and structure are not preserved."
    ] = "Lossy excerpt: text may be omitted; tabs, formatting, and structure are not preserved."


class DocumentResponse(McpResponse):
    document: SearchResult
    can_edit: bool = Field(strict=True)
    content_trust: Literal["untrusted_document_data"] = "untrusted_document_data"
    excerpt: DocumentExcerpt | None = None


class PolishInput(McpModel):
    file_id: McpFileId
    expected_modified_time: Freshness
    mode: TransformMode
    output_strategy: OutputStrategy
    idempotency_key: IdempotencyKey
    output_name: OutputName | None = None

    @field_validator("expected_modified_time")
    @classmethod
    def require_aware_freshness(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            msg = "Freshness must be an ISO timestamp with an explicit timezone"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_shared_request(self) -> Self:
        self.to_transform_request()
        return self

    def to_transform_request(self) -> DocumentTransformRequest:
        return DocumentTransformRequest(
            source=DriveFileSource(
                kind="drive_file",
                file_id=self.file_id,
                expected_modified_time=self.expected_modified_time,
            ),
            mode=self.mode,
            output_strategy=self.output_strategy,
            output_name=self.output_name,
        )


class MarkdownImportInput(McpModel):
    content: MarkdownContent
    name: OutputName
    destination_folder_id: McpFileId
    mode: TransformMode
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def validate_shared_request(self) -> Self:
        self.to_transform_request()
        return self

    def to_transform_request(self) -> DocumentTransformRequest:
        return DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content=self.content,
                name=self.name,
                destination_folder_id=self.destination_folder_id,
            ),
            mode=self.mode,
            output_strategy=OutputStrategy.COPY,
        )


class AdmissionInput(McpModel):
    idempotency_key: IdempotencyKey


class AdmissionResponse(McpResponse):
    admission: DocumentTransformAdmission | None


class TaskInput(McpModel):
    task_id: TaskId


class TaskResponse(McpResponse):
    task: TaskRead
    retry_after_seconds: int | None = Field(default=None, ge=1, le=3600, strict=True)
    content_trust: Literal["untrusted_document_data"] = "untrusted_document_data"

    @model_validator(mode="after")
    def require_transform_task(self) -> Self:
        if self.task.kind is not TaskKind.FILE_DOCUMENT_TRANSFORM:
            msg = "Only document transformation tasks are exposed"
            raise ValueError(msg)
        if self.task.result is not None and not isinstance(
            self.task.result, DocumentTransformResult
        ):
            msg = "Only document transformation results are exposed"
            raise ValueError(msg)
        return self


class PolishResponse(TaskResponse):
    replayed: bool = Field(strict=True)


class ShortLinkInput(McpModel):
    file_id: McpFileId


class ShortLinkResponse(McpResponse):
    file_id: McpFileId
    alias: DocumentAliasValue | None
    wiki_url: WikiUrl | None

    @model_validator(mode="after")
    def validate_alias_presence(self) -> Self:
        if (self.alias is None) != (self.wiki_url is None):
            msg = "Short-link URL must be present exactly when an alias exists"
            raise ValueError(msg)
        return self
