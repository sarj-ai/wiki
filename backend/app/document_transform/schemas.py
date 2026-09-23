from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self
import uuid

from pydantic import (
    AwareDatetime,
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


class QaDestinationPermissionBinding(StrictTransformModel):
    version: Annotated[str, StringConstraints(pattern=r"^[0-9]+$", max_length=64)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class QaFilePermissionEvidence(StrictTransformModel):
    file_id: FileId
    mime_type: str = Field(min_length=1, max_length=255)
    parent_folder_id: FileId | None
    version: Annotated[str, StringConstraints(pattern=r"^[0-9]+$", max_length=64)]
    permission_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    observed_at: AwareDatetime


class QaCopyPermissionEvidence(StrictTransformModel):
    status: Literal["verified_owner_only"] = "verified_owner_only"
    operation_id: uuid.UUID
    source_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    output_revision_id: DocsRevisionId
    destination: QaFilePermissionEvidence
    output: QaFilePermissionEvidence


class QaQuarantinedOutputEvidence(StrictTransformModel):
    status: Literal["quarantined_unverified"] = "quarantined_unverified"
    operation_id: uuid.UUID
    source_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    output_file_id: FileId
    destination_folder_id: FileId
    output_revision_id: DocsRevisionId
    observed_at: AwareDatetime
    reason: Literal["owner_only_readback_failed"] = "owner_only_readback_failed"
    manual_recovery_required: Literal[True] = True


class DocumentTransformRequest(StrictTransformModel):
    source: TransformSource = Field(
        description="Inline Markdown, a signed GCS upload reference, or a Drive file."
    )
    mode: TransformMode = Field(
        description="format applies document styling; unslop conservatively edits prose; both runs both."
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


class QaDocumentTransformRequest(DocumentTransformRequest):
    qa_contract: Literal["owner_only_v1"] = "owner_only_v1"
    qa_destination_permission: QaDestinationPermissionBinding

    @model_validator(mode="after")
    def validate_qa_destination(self) -> Self:
        if (
            self.copy_destination_folder_id is None
            or self.output_strategy is not OutputStrategy.COPY
            or not isinstance(self.source, DriveFileSource)
        ):
            code = "qa_destination_permission_source"
            msg = "QA permission binding requires a Drive file copy destination"
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


class CurrentDocumentPolish(StrictTransformModel):
    result: DocumentTransformResult | None


class QaDocumentTransformResult(DocumentTransformResult):
    qa_permission_evidence: QaCopyPermissionEvidence

    @model_validator(mode="after")
    def validate_owner_only_scope(self) -> Self:
        if self.access_scope != "owner_only":
            msg = "QA transform result requires owner-only access scope"
            raise ValueError(msg)
        return self


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


class CopyCheckpointStage(StrEnum):
    PLANNED = "planned"
    CREATING = "creating"
    CREATED = "created"
    VERIFIED = "verified"
    ATTESTED = "attested"
    QUARANTINED = "quarantined"


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
    output_parent_folder_id: FileId | None = None
    qa_destination_permission: QaDestinationPermissionBinding | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    qa_permission_evidence: QaCopyPermissionEvidence | None = None
    qa_quarantine: QaQuarantinedOutputEvidence | None = None
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
    result: DocumentTransformResult | QaDocumentTransformResult | None = None
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
        has_output = self.output is not None and self.output_revision_id is not None
        if self.stage in {
            CopyCheckpointStage.CREATED,
            CopyCheckpointStage.VERIFIED,
            CopyCheckpointStage.ATTESTED,
            CopyCheckpointStage.QUARANTINED,
        }:
            if not has_output:
                msg = "created copy checkpoint requires an output and revision"
                raise ValueError(msg)
        elif self.output is not None or self.output_revision_id is not None:
            msg = "pre-create copy checkpoint cannot contain an output"
            raise ValueError(msg)
        if (self.stage in {CopyCheckpointStage.VERIFIED, CopyCheckpointStage.ATTESTED}) != (
            self.result is not None
        ):
            msg = "only a verified or attested copy checkpoint contains a result"
            raise ValueError(msg)
        self._validate_qa_stage()
        if (
            self.output_parent_folder_id is not None
            and self.output is not None
            and self.output.parent_folder_id != self.output_parent_folder_id
        ):
            msg = "copy checkpoint output must remain in its planned destination"
            raise ValueError(msg)
        return self

    def _validate_qa_stage(self) -> None:
        if self.qa_permission_evidence is not None and self.qa_quarantine is not None:
            msg = "copy checkpoint cannot be both permission-verified and quarantined"
            raise ValueError(msg)
        if self.stage is CopyCheckpointStage.ATTESTED and self.qa_destination_permission is None:
            msg = "attested copy checkpoint requires an internal QA permission binding"
            raise ValueError(msg)
        if (
            self.qa_destination_permission is not None
            and self.stage is CopyCheckpointStage.ATTESTED
        ):
            if self.qa_permission_evidence is None or not isinstance(
                self.result, QaDocumentTransformResult
            ):
                msg = "attested QA copy requires owner-only permission evidence"
                raise ValueError(msg)
            if self.result.qa_permission_evidence != self.qa_permission_evidence:
                msg = "QA permission evidence must match the terminal result"
                raise ValueError(msg)
        if self.qa_quarantine is not None and self.stage is not CopyCheckpointStage.QUARANTINED:
            msg = "quarantine evidence requires a quarantined checkpoint"
            raise ValueError(msg)
        if self.stage is CopyCheckpointStage.QUARANTINED and self.qa_quarantine is None:
            msg = "quarantined copy checkpoint requires recovery evidence"
            raise ValueError(msg)
        if self.stage is CopyCheckpointStage.QUARANTINED and self.qa_destination_permission is None:
            msg = "quarantined copy checkpoint requires an internal QA permission binding"
            raise ValueError(msg)


class WordCopyCheckpoint(StrictTransformModel):
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
    stage: Literal[
        CopyCheckpointStage.PLANNED,
        CopyCheckpointStage.CREATING,
        CopyCheckpointStage.CREATED,
        CopyCheckpointStage.VERIFIED,
    ]
    output: DocumentOutput | None = None
    result: DocumentTransformResult | None = None

    @model_validator(mode="after")
    def validate_stage(
        self,
    ) -> Self:
        if not self.reserved_output_id or self.reserved_output_id == self.source_binding.get("id"):
            message = "Word copy requires a distinct reserved output ID"
            raise ValueError(message)
        created = self.stage in {CopyCheckpointStage.CREATED, CopyCheckpointStage.VERIFIED}
        if created != (self.output is not None):
            message = "Word checkpoint output does not match its stage"
            raise ValueError(message)
        if self.output is not None and self.output.file_id != self.reserved_output_id:
            message = "Word output does not match its reserved file ID"
            raise ValueError(message)
        if (self.stage is CopyCheckpointStage.VERIFIED) != (self.result is not None):
            message = "Only verified Word checkpoints contain a result"
            raise ValueError(message)
        return self


DocumentTransformCheckpoint = Annotated[
    InPlaceCheckpoint | CopyCheckpoint | WordCopyCheckpoint,
    Field(discriminator="kind"),
]
