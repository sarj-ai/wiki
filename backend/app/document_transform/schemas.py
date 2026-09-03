from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.identifiers import FileId


SourceKind = Literal["drive_file", "markdown"]


class TransformMode(StrEnum):
    FORMAT = "format"
    UNSLOP = "unslop"
    BOTH = "both"


class TransformPhase(StrEnum):
    FORMAT = "format"
    UNSLOP = "unslop"


MarkdownContent = Annotated[str, StringConstraints(min_length=1, max_length=524_288)]
OutputName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class StrictTransformModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class TextPatch(StrictTransformModel):
    block_id: str
    expected_sha256: str
    replacement: str
    rationale: Annotated[str, StringConstraints(strip_whitespace=True, max_length=240)] = ""

    @field_validator("replacement")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            msg = "replacement cannot contain NUL"
            raise ValueError(msg)
        return value


class PatchEnvelope(StrictTransformModel):
    patches: list[TextPatch] = Field(max_length=500)


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
    model_ids: list[str]
    tool_versions: dict[str, str]
    pipeline_version: str
    needs_review: bool

    @property
    def file(self) -> DocumentOutput:
        return self.output
