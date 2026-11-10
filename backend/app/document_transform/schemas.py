from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictBool,
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
from app.document_transform.custom_furniture import CustomFurnitureBinding, CustomHeaderTitleTarget
from app.document_transform.header_provenance import (
    HeaderIdentityEvidence,
    HeaderIdentityFinalization,
)
from app.document_transform.ir import MAX_DOC_TABS, MAX_DOCUMENT_BLOCKS
from app.document_transform.limits import MAX_MARKDOWN_BYTES
from app.document_transform.native_date_flow import NativeDateCheckpoint
from app.document_transform.native_font_proof import NativeFontBlockId, NativeFontProof
from app.document_transform.native_footer_flow import NativeFooterCheckpoint
from app.document_transform.native_pagination import MAX_PAGINATION_RULES, NativePaginationRule
from app.document_transform.native_paragraph_layout import NativeParagraphLayout
from app.document_transform.native_table_font import TableFontPlan
from app.document_transform.readability_types import ReadabilityCheckpoint
from app.document_transform.tab_names import TabNamingPlan
from app.document_transform.tab_structure_execution import StructureCheckpoint
from app.identifiers import BrandProfileId, DocsRevisionId, FileId, ModelId


SourceKind = Literal["drive_file", "markdown", "uploaded_markdown"]
ArtifactKind = Literal["document", "presentation"]
AccessScope = Literal["inherited", "owner_only"]
TabNamingDeclinedReason = Literal["unsupported_metadata"]
_MAX_IN_PLACE_FILE_ID_LENGTH = 128


class TransformMode(StrEnum):
    FORMAT = "format"
    UNSLOP = "unslop"
    BOTH = "both"


class TransformPhase(StrEnum):
    FORMAT = "format"
    UNSLOP = "unslop"


class FindingSeverity(StrEnum):
    INFORMATIONAL = "informational"
    REVIEW = "review"


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
    file_id: FileId = Field(description="ID of the Drive file to transform.")
    expected_modified_time: str = Field(
        description="Drive modifiedTime read before submission; rejects stale requests."
    )


class MarkdownSource(StrictTransformModel):
    kind: Literal["markdown", "uploaded_markdown"]
    content: str = Field(
        default="",
        exclude_if=lambda value: not value,
        max_length=MAX_MARKDOWN_BYTES,
        description="Inline UTF-8 Markdown, up to 512 KiB; omitted for uploads.",
    )
    upload_id: uuid.UUID | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="ID returned by POST /document-transforms/uploads.",
    )
    name: OutputName = Field(description="Title of the new Google Doc.")
    destination_folder_id: FileId = Field(
        description="Writable Google Drive folder in which to create the document."
    )

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

    @model_validator(mode="after")
    def validate_markdown_source(self) -> Self:
        if self.kind == "markdown" and self.content and self.upload_id is None:
            return self
        if self.kind == "uploaded_markdown" and not self.content and self.upload_id is not None:
            return self
        code = "markdown_source"
        msg = "Inline Markdown needs content; uploaded Markdown needs upload_id only"
        raise PydanticCustomError(code, msg)


class MarkdownUpload(StrictTransformModel):
    upload_id: uuid.UUID
    url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_in_seconds: int


TransformSource = Annotated[DriveFileSource | MarkdownSource, Field(discriminator="kind")]


class DocumentTransformRequest(StrictTransformModel):
    source: TransformSource = Field(
        description="Inline Markdown, a signed GCS upload reference, or a Drive file."
    )
    mode: TransformMode = Field(
        description="format applies document styling without changing wording; unslop aggressively shortens prose with fact-preservation review; both runs Unslop and then Format."
    )
    output_name: OutputName | None = Field(
        default=None, description="Optional name for a Drive file copy; Markdown uses source.name."
    )
    output_strategy: OutputStrategy = Field(
        default=OutputStrategy.COPY,
        description="copy creates a sibling; in_place is available only for eligible native Google Docs.",
    )
    copy_destination_folder_id: FileId | None = Field(
        default=None, description="Optional destination for a Drive file copy."
    )

    @model_validator(mode="after")
    def validate_output_strategy(self) -> Self:
        if self.copy_destination_folder_id is not None and (
            self.output_strategy is not OutputStrategy.COPY
            or not isinstance(self.source, DriveFileSource)
        ):
            code = "copy_destination_source"
            msg = "copy_destination_folder_id is allowed only for Drive file copies"
            raise PydanticCustomError(code, msg)
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


class TextPatchReview(StrictTransformModel):
    block_id: str
    expected_sha256: str
    replacement_sha256: str
    equivalent: StrictBool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=400)]


class PatchReviewEnvelope(StrictTransformModel):
    reviews: list[TextPatchReview] = Field(max_length=100)


class TransformFinding(StrictTransformModel):
    code: str
    block_id: str | None = None
    detail: str
    severity: FindingSeverity = Field(
        default=FindingSeverity.REVIEW,
        exclude_if=lambda value: value is FindingSeverity.REVIEW,
    )


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
    promoted_heading_count: int = Field(default=0, ge=0, le=MAX_DOCUMENT_BLOCKS)
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


class CurrentDocumentPolish(StrictTransformModel):
    result: DocumentTransformResult | None


class TabNamingCheckpoint(StrictTransformModel):
    plan: TabNamingPlan
    revision_id: DocsRevisionId
    marker_name: str
    marker_range: dict[str, JsonValue]
    invariant_sha256: str
    invariant_version: Literal["stable-images-v2"] | None = None


class InPlaceCheckpointStage(StrEnum):
    HEADERS = "headers"
    CONTENT = "content"
    COMPLETE = "complete"


class InPlacePlan(StrictTransformModel):
    readability: ReadabilityCheckpoint | None = None
    readability_result: DocumentTransformResult | None = None
    visual_authority_block_ids: tuple[
        Annotated[NativeFontBlockId, StringConstraints(pattern=r"^b[0-9]+$", max_length=12)], ...
    ] = Field(default=(), max_length=MAX_DOCUMENT_BLOCKS)
    preserved_font_block_ids: tuple[
        Annotated[NativeFontBlockId, StringConstraints(pattern=r"^b[0-9]+$", max_length=12)], ...
    ] = Field(default=(), max_length=MAX_DOCUMENT_BLOCKS)
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
    custom_header_titles: tuple[CustomHeaderTitleTarget, ...] = Field(
        default=(), max_length=MAX_DOC_TABS * 3
    )
    custom_header_title_targets: tuple[CustomHeaderTitleTarget, ...] = Field(
        default=(), max_length=MAX_DOC_TABS * 3
    )
    custom_header_titles_required: bool = False
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
    tab_naming_declined_reason: TabNamingDeclinedReason | None = None

    @model_validator(mode="after")
    def validate_tab_naming_state(self) -> Self:
        if self.tab_naming is not None and self.tab_naming_declined_reason is not None:
            msg = "tab naming cannot be both planned and declined"
            raise ValueError(msg)
        titles = {(item.tab_id, item.header_id) for item in self.custom_header_titles}
        targets = {(item.tab_id, item.header_id) for item in self.custom_header_title_targets}
        furniture_headers = {
            (binding.tab_id, header_id)
            for binding in self.custom_furniture
            for header_id in binding.header_ids
        }
        if len(titles) != len(self.custom_header_titles) or len(targets) != len(
            self.custom_header_title_targets
        ):
            msg = "custom header title bindings must be unique"
            raise ValueError(msg)
        if not targets <= titles:
            msg = "custom header title targets must belong to the verified title set"
            raise ValueError(msg)
        if self.custom_header_titles_required and titles != furniture_headers:
            msg = "every preserved custom header must have a verified native title"
            raise ValueError(msg)
        if not self.custom_header_titles_required and (titles or targets):
            msg = "unbranded transforms cannot manage custom header titles"
            raise ValueError(msg)
        return self


class InPlaceMutation(InPlacePlan):
    marker_name: str
    marker_range: dict[str, JsonValue]
    mutation_sha256: str
    requests: list[dict[str, JsonValue]] = Field(min_length=1)


class HeaderInPlaceCheckpoint(InPlaceMutation):
    stage: Literal[InPlaceCheckpointStage.HEADERS] = InPlaceCheckpointStage.HEADERS


class ContentInPlaceCheckpoint(InPlaceMutation):
    stage: Literal[InPlaceCheckpointStage.CONTENT] = InPlaceCheckpointStage.CONTENT


class NonmutatingInPlacePlan(InPlacePlan):
    marker_name: None = None
    marker_range: None = None
    mutation_sha256: None = None
    requests: tuple[()] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_unused_markers(cls, value: object) -> object:
        if (
            isinstance(value, dict)
            and value.get("stage") in {"content", "complete"}
            and value.get("requests") == []
        ):
            return {**value, "marker_name": None, "marker_range": None, "mutation_sha256": None}
        return value


class NoopContentInPlaceCheckpoint(NonmutatingInPlacePlan):
    stage: Literal[InPlaceCheckpointStage.CONTENT] = InPlaceCheckpointStage.CONTENT


class CompletedInPlaceCheckpoint(NonmutatingInPlacePlan):
    stage: Literal[InPlaceCheckpointStage.COMPLETE] = InPlaceCheckpointStage.COMPLETE


InPlaceCheckpoint = (
    HeaderInPlaceCheckpoint
    | ContentInPlaceCheckpoint
    | NoopContentInPlaceCheckpoint
    | CompletedInPlaceCheckpoint
)


class InPlaceCheckpointPayload(RootModel[InPlaceCheckpoint]):
    @model_validator(mode="before")
    @classmethod
    def normalize_nonmutating_plan(cls, value: object) -> object:
        # Old checkpoints retained unused markers after a no-op content plan.
        # They never authorized a write. Normalize only that exact harmless shape.
        if (
            isinstance(value, dict)
            and value.get("stage") in {"content", "complete"}
            and value.get("requests") == []
        ):
            return {**value, "marker_name": None, "marker_range": None, "mutation_sha256": None}
        return value


def transition_in_place(
    checkpoint: InPlaceCheckpoint,
    *,
    stage: InPlaceCheckpointStage,
    current_revision_id: DocsRevisionId,
    requests: list[dict[str, JsonValue]],
    marker_name: str | None,
    marker_range: dict[str, JsonValue] | None,
    mutation_sha256: str | None,
    headers: dict[str, str] | None = None,
) -> InPlaceCheckpoint:
    allowed = {
        InPlaceCheckpointStage.HEADERS: {InPlaceCheckpointStage.CONTENT},
        InPlaceCheckpointStage.CONTENT: {InPlaceCheckpointStage.COMPLETE},
        InPlaceCheckpointStage.COMPLETE: set(),
    }
    if stage not in allowed[checkpoint.stage]:
        message = "Invalid in-place checkpoint transition"
        raise ValueError(message)
    payload = {
        **checkpoint.model_dump(),
        "stage": stage,
        "current_revision_id": current_revision_id,
        "requests": requests,
        "marker_name": marker_name,
        "marker_range": marker_range,
        "mutation_sha256": mutation_sha256,
    }
    if headers is not None:
        payload["headers"] = headers
    return InPlaceCheckpointPayload.model_validate(payload).root


class CopyCheckpointStage(StrEnum):
    PLANNED = "planned"
    CREATING = "creating"
    CREATED = "created"
    VERIFIED = "verified"


class CopyPlan(StrictTransformModel):
    readability: ReadabilityCheckpoint | None = None
    readability_result: DocumentTransformResult | None = None
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
    output_parent_folder_id: FileId | None = None
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
    tab_naming: TabNamingCheckpoint | None = None
    tab_naming_declined_reason: TabNamingDeclinedReason | None = None
    native_footer: NativeFooterCheckpoint | None = None
    native_dates: NativeDateCheckpoint | None = None
    tab_structure: StructureCheckpoint | None = None

    @model_validator(mode="after")
    def validate_stage(
        self,
    ) -> Self:
        if self.tab_naming is not None and self.tab_naming_declined_reason is not None:
            msg = "tab naming cannot be both planned and declined"
            raise ValueError(msg)
        return self


class UncreatedCopyPlan(CopyPlan):
    output: None = None
    output_revision_id: None = None
    result: None = None


class PlannedCopyCheckpoint(UncreatedCopyPlan):
    stage: Literal[CopyCheckpointStage.PLANNED] = CopyCheckpointStage.PLANNED


class CreatingCopyCheckpoint(UncreatedCopyPlan):
    stage: Literal[CopyCheckpointStage.CREATING] = CopyCheckpointStage.CREATING


class CreatedCopyPlan(CopyPlan):
    output: DocumentOutput
    output_revision_id: DocsRevisionId

    @model_validator(mode="after")
    def validate_destination(self) -> Self:
        if (
            self.output_parent_folder_id is not None
            and self.output.parent_folder_id != self.output_parent_folder_id
        ):
            message = "copy checkpoint output must remain in its planned destination"
            raise ValueError(message)
        return self


class CreatedCopyCheckpoint(CreatedCopyPlan):
    stage: Literal[CopyCheckpointStage.CREATED] = CopyCheckpointStage.CREATED
    result: None = None


class VerifiedCopyCheckpoint(CreatedCopyPlan):
    stage: Literal[CopyCheckpointStage.VERIFIED] = CopyCheckpointStage.VERIFIED
    result: DocumentTransformResult


CopyCheckpoint = Annotated[
    PlannedCopyCheckpoint | CreatingCopyCheckpoint | CreatedCopyCheckpoint | VerifiedCopyCheckpoint,
    Field(discriminator="stage"),
]


class CopyCheckpointPayload(RootModel[CopyCheckpoint]):
    pass


class WordCopyPlan(StrictTransformModel):
    version: Literal[1] = 1
    kind: Literal["word_copy"] = "word_copy"
    request_sha256: str
    source_binding: dict[str, str]
    source_sha256: str
    output_sha256: str
    transformer_version: str
    target_font: str
    output_name: OutputName
    reserved_output_id: FileId
    changed_run_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_stage(
        self,
    ) -> Self:
        if not self.reserved_output_id or self.reserved_output_id == self.source_binding.get("id"):
            message = "Word copy requires a distinct reserved output ID"
            raise ValueError(message)
        return self


class UncreatedWordPlan(WordCopyPlan):
    output: None = None
    result: None = None


class PlannedWordCheckpoint(UncreatedWordPlan):
    stage: Literal[CopyCheckpointStage.PLANNED] = CopyCheckpointStage.PLANNED


class CreatingWordCheckpoint(UncreatedWordPlan):
    stage: Literal[CopyCheckpointStage.CREATING] = CopyCheckpointStage.CREATING


class CreatedWordPlan(WordCopyPlan):
    output: DocumentOutput

    @model_validator(mode="after")
    def validate_output_identity(self) -> Self:
        if self.output.file_id != self.reserved_output_id:
            message = "Word output does not match its reserved file ID"
            raise ValueError(message)
        return self


class CreatedWordCheckpoint(CreatedWordPlan):
    stage: Literal[CopyCheckpointStage.CREATED] = CopyCheckpointStage.CREATED
    result: None = None


class VerifiedWordCheckpoint(CreatedWordPlan):
    stage: Literal[CopyCheckpointStage.VERIFIED] = CopyCheckpointStage.VERIFIED
    result: DocumentTransformResult


WordCopyCheckpoint = Annotated[
    PlannedWordCheckpoint | CreatingWordCheckpoint | CreatedWordCheckpoint | VerifiedWordCheckpoint,
    Field(discriminator="stage"),
]


class WordCheckpointPayload(RootModel[WordCopyCheckpoint]):
    pass


DocumentTransformCheckpoint = InPlaceCheckpoint | CopyCheckpoint | WordCopyCheckpoint
