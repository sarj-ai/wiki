from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.document_transform.brand_manifest import (
    CURRENT_BRAND_MANIFEST,
    CURRENT_BRAND_PROFILE_ID,
)
from app.identifiers import BrandProfileId, DocsRevisionId, FileId, ModelId


SourceKind = Literal["drive_file", "markdown"]
_MAX_IN_PLACE_FILE_ID_LENGTH = 128


class TransformMode(StrEnum):
    FORMAT = "format"
    UNSLOP = "unslop"
    BOTH = "both"


class TransformPhase(StrEnum):
    FORMAT = "format"
    UNSLOP = "unslop"


class OutputStrategy(StrEnum):
    COPY = "copy"
    IN_PLACE = "in_place"


MarkdownContent = Annotated[str, StringConstraints(min_length=1, max_length=524_288)]
OutputName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class StrictTransformModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentTransformCapabilities(StrictTransformModel):
    copy_supported: Literal[True] = Field(default=True, alias="copy")
    in_place: bool


class DriveFileSource(StrictTransformModel):
    kind: Literal["drive_file"]
    file_id: FileId
    expected_modified_time: str


class MarkdownSource(StrictTransformModel):
    kind: Literal["markdown"]
    content: MarkdownContent
    name: OutputName
    destination_folder_id: FileId


TransformSource = Annotated[DriveFileSource | MarkdownSource, Field(discriminator="kind")]


class DocumentTransformRequest(StrictTransformModel):
    source: TransformSource
    mode: TransformMode
    output_name: OutputName | None = None
    output_strategy: OutputStrategy = OutputStrategy.COPY

    @model_validator(mode="after")
    def validate_output_strategy(self) -> Self:
        if self.output_strategy is OutputStrategy.COPY:
            return self
        if not isinstance(self.source, DriveFileSource):
            code = "in_place_source"
            msg = "In-place transformation requires a native Google Doc source"
            raise PydanticCustomError(code, msg)
        if not 1 <= len(self.source.file_id) <= _MAX_IN_PLACE_FILE_ID_LENGTH:
            code = "in_place_file_id"
            msg = "In-place Google Drive file_id must be between 1 and 128 characters"
            raise PydanticCustomError(code, msg)
        if self.output_name is not None:
            code = "in_place_output_name"
            msg = "output_name is not allowed for in-place transformation"
            raise PydanticCustomError(code, msg)
        return self


class TextPatch(StrictTransformModel):
    block_id: str
    expected_sha256: str
    replacement: Annotated[str, StringConstraints(max_length=524_288)]
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)] = ""

    @field_validator("replacement")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            msg = "replacement cannot contain NUL"
            raise ValueError(msg)
        return value


class PatchEnvelope(StrictTransformModel):
    patches: list[TextPatch] = Field(max_length=100)


class TransformFinding(StrictTransformModel):
    code: str
    block_id: str | None = None
    detail: str


class ValidationSummary(StrictTransformModel):
    source_unchanged: bool
    protected_content_preserved: bool
    structure_preserved: bool
    unsafe_patch_count: int = Field(ge=0)


class DocumentOutput(StrictTransformModel):
    file_id: FileId
    name: str
    parent_folder_id: FileId
    web_url: str | None = None

    @property
    def id(self) -> FileId:
        return self.file_id


class DocumentTransformResult(StrictTransformModel):
    operation_id: uuid.UUID
    source_sha256: str
    output: DocumentOutput
    mode: TransformMode
    applied_patch_count: int = Field(ge=0)
    findings: list[TransformFinding]
    validation: ValidationSummary
    model_ids: list[ModelId]
    tool_versions: dict[str, str]
    pipeline_version: str
    needs_review: bool
    brand_profile_id: BrandProfileId = CURRENT_BRAND_PROFILE_ID
    brand_profile_sha256: str = CURRENT_BRAND_MANIFEST.sha256
    deterministic_patch_count: int = Field(default=0, ge=0)
    model_patch_count: int = Field(default=0, ge=0)
    llm_invoked: bool = False
    output_strategy: OutputStrategy = OutputStrategy.COPY
    changed: bool = True
    source_revision_id: DocsRevisionId | None = None
    output_revision_id: DocsRevisionId | None = None

    @property
    def file(self) -> DocumentOutput:
        return self.output


class InPlaceCheckpointStage(StrEnum):
    HEADERS = "headers"
    CONTENT = "content"
    COMPLETE = "complete"


class InPlaceCheckpoint(StrictTransformModel):
    version: Literal[1] = 1
    document_id: FileId
    source_sha256: str
    source_modified_time: str
    brand_profile_id: BrandProfileId
    brand_profile_sha256: str
    pipeline_version: str
    request_builder_version: str
    unslop_catalog_sha256: str
    original_semantic_sha256: str
    expected_semantic_sha256: str
    structural_sha256: str
    protected_structure_sha256: str
    body_sha256: str
    header_safe_sha256: str
    source_revision_id: DocsRevisionId
    current_revision_id: DocsRevisionId
    stage: InPlaceCheckpointStage
    marker_name: str | None = None
    marker_range: dict[str, Any] | None = None
    mutation_sha256: str | None = None
    requests: list[dict[str, Any]] = Field(default_factory=list)
    replacements: dict[str, str]
    findings: list[TransformFinding]
    applied_patch_count: int = Field(ge=0)
    deterministic_patch_count: int = Field(ge=0)
    model_patch_count: int = Field(ge=0)
    llm_invoked: bool
    model_ids: list[ModelId]
    headers: dict[str, str] = Field(default_factory=dict)
    changed: bool
