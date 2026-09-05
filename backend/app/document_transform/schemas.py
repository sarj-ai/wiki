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

from app.aliases.schemas import DocumentAliasValue
from app.document_transform.authored_labels import AuthoredLabelRole
from app.document_transform.brand_manifest import (
    CURRENT_BRAND_MANIFEST,
    CURRENT_BRAND_PROFILE_ID,
)
from app.document_transform.custom_furniture import CustomFurnitureBinding
from app.document_transform.header_provenance import (
    HeaderIdentityEvidence,
    HeaderIdentityFinalization,
)
from app.document_transform.ir import MAX_DOC_TABS, MAX_DOCUMENT_BLOCKS
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.document_transform.native_date_flow import NativeDateCheckpoint
from app.document_transform.native_font_proof import NativeFontProof
from app.document_transform.native_footer_flow import NativeFooterCheckpoint
from app.document_transform.native_pagination import MAX_PAGINATION_RULES, NativePaginationRule
from app.document_transform.native_paragraph_layout import NativeParagraphLayout
from app.document_transform.native_table_font import TableFontPlan
from app.document_transform.tab_names import TabNamingPlan
from app.document_transform.tab_structure_execution import StructureCheckpoint
from app.identifiers import BrandProfileId, DocsRevisionId, FileId, ModelId


SourceKind = Literal["drive_file", "markdown"]
ArtifactKind = Literal["document", "presentation"]
AccessScope = Literal["inherited", "owner_only"]
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


MarkdownContent = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_MARKDOWN_BYTES),
]
OutputName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class StrictTransformModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentTransformCapabilities(StrictTransformModel):
    copy_supported: Literal[True] = Field(default=True, alias="copy")
    in_place: bool
    presentation_copy: Literal[True] = True
    presentation_in_place: Literal[False] = False


class DriveFileSource(StrictTransformModel):
    kind: Literal["drive_file"]
    file_id: FileId
    expected_modified_time: str


class MarkdownSource(StrictTransformModel):
    kind: Literal["markdown"]
    content: MarkdownContent
    name: OutputName
    destination_folder_id: FileId

    @field_validator("content")
    @classmethod
    def bound_utf8_content(cls, value: str) -> str:
        try:
            size = len(value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError as exc:
            code = "markdown_utf8"
            msg = "Markdown must be valid UTF-8 text"
            raise PydanticCustomError(code, msg) from exc
        if size > MAX_MARKDOWN_BYTES:
            code = "markdown_bytes"
            msg = "Markdown must not exceed {limit} UTF-8 bytes"
            raise PydanticCustomError(code, msg, {"limit": MAX_MARKDOWN_BYTES})
        return value


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
    media_type: str = "application/vnd.google-apps.document"

    @property
    def id(self) -> FileId:
        return self.file_id


class DocumentTransformResult(StrictTransformModel):
    promoted_heading_count: int = Field(default=0, ge=0, le=20)
    header_identity: HeaderIdentityEvidence | None = None
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
    artifact_kind: ArtifactKind = "document"
    access_scope: AccessScope = "inherited"
    short_link_alias: DocumentAliasValue | None = None
    short_link_status: Literal["ready", "unavailable"] | None = None

    @property
    def file(self) -> DocumentOutput:
        return self.output


class TabNamingCheckpoint(StrictTransformModel):
    plan: TabNamingPlan
    revision_id: DocsRevisionId
    marker_name: str
    marker_range: dict[str, Any]
    invariant_sha256: str


class InPlaceCheckpointStage(StrEnum):
    HEADERS = "headers"
    CONTENT = "content"
    COMPLETE = "complete"


class InPlaceCheckpoint(StrictTransformModel):
    paragraph_layout: tuple[NativeParagraphLayout, ...] = Field(
        default=(), max_length=MAX_DOCUMENT_BLOCKS
    )
    table_font_plan: TableFontPlan | None = None
    native_font_proof: tuple[NativeFontProof, ...] | None = Field(
        default=None, max_length=MAX_DOCUMENT_BLOCKS
    )
    custom_furniture: tuple[CustomFurnitureBinding, ...] = Field(
        default=(), max_length=MAX_DOC_TABS
    )
    pagination: tuple[NativePaginationRule, ...] = Field(
        default=(), max_length=MAX_PAGINATION_RULES
    )
    header_identity: HeaderIdentityEvidence | None = None
    version: Literal[2] = 2
    kind: Literal["in_place"] = "in_place"
    document_id: FileId
    source_title: str
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
    preserved_headers: dict[str, str] = Field(default_factory=dict)
    changed: bool
    tab_naming: TabNamingCheckpoint | None = None


class CopyCheckpointStage(StrEnum):
    PLANNED = "planned"
    CREATING = "creating"
    CREATED = "created"
    VERIFIED = "verified"


class CopyCheckpoint(StrictTransformModel):
    pagination: tuple[NativePaginationRule, ...] = Field(
        default=(), max_length=MAX_PAGINATION_RULES
    )
    authored_labels: tuple[AuthoredLabelRole, ...] = Field(default=(), max_length=100)
    header_finalization: HeaderIdentityFinalization | None = None
    managed_headers: dict[str, str] | None = None
    """Durable copy plan and output attestation.

    A ``creating`` checkpoint is deliberately ambiguous: retries may recover its
    output, but must never issue another create whose first outcome is unknown.
    """

    version: Literal[1] = 1
    kind: Literal["copy"] = "copy"
    source_title: str
    source_sha256: str
    source_modified_time: str | None
    source_revision_id: DocsRevisionId | None
    source_parent_folder_id: FileId
    brand_profile_id: BrandProfileId
    brand_profile_sha256: str
    pipeline_version: str
    request_builder_version: str
    unslop_catalog_sha256: str
    original_semantic_sha256: str
    expected_semantic_sha256: str
    plan_sha256: str
    manifest_sha256: str
    logo_sha256: str | None
    output_name: OutputName
    replacements: dict[str, str]
    findings: list[TransformFinding]
    applied_patch_count: int = Field(ge=0)
    deterministic_patch_count: int = Field(ge=0)
    model_patch_count: int = Field(ge=0)
    llm_invoked: bool
    model_ids: list[ModelId]
    stage: CopyCheckpointStage
    output: DocumentOutput | None = None
    output_revision_id: DocsRevisionId | None = None
    result: DocumentTransformResult | None = None
    tab_naming: TabNamingCheckpoint | None = None
    native_footer: NativeFooterCheckpoint | None = None
    native_dates: NativeDateCheckpoint | None = None
    tab_structure: StructureCheckpoint | None = None

    @model_validator(mode="after")
    def validate_stage(self) -> Self:
        has_output = self.output is not None and self.output_revision_id is not None
        if self.stage in {CopyCheckpointStage.CREATED, CopyCheckpointStage.VERIFIED}:
            if not has_output:
                msg = "created copy checkpoint requires an output and revision"
                raise ValueError(msg)
        elif self.output is not None or self.output_revision_id is not None:
            msg = "pre-create copy checkpoint cannot contain an output"
            raise ValueError(msg)
        if (self.stage is CopyCheckpointStage.VERIFIED) != (self.result is not None):
            msg = "only a verified copy checkpoint contains a result"
            raise ValueError(msg)
        return self


DocumentTransformCheckpoint = Annotated[
    InPlaceCheckpoint | CopyCheckpoint,
    Field(discriminator="kind"),
]
