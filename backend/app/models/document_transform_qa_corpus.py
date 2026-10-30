from datetime import datetime
from typing import NewType
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import (
    DocsRevisionId,
    DriveId,
    FileId,
    IdempotencyKey,
    QaPlanId,
    QaRunId,
    TaskId,
    UserId,
)
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId


DocumentTransformQaCorpusCampaignId = NewType("DocumentTransformQaCorpusCampaignId", uuid.UUID)
DocumentTransformQaCorpusMemberId = NewType("DocumentTransformQaCorpusMemberId", uuid.UUID)
DocumentTransformQaCorpusSubmissionId = NewType("DocumentTransformQaCorpusSubmissionId", uuid.UUID)
DocumentTransformQaExecutionUid = NewType("DocumentTransformQaExecutionUid", uuid.UUID)


class DocumentTransformQaCorpusCampaign(Base):
    __tablename__ = "document_transform_qa_corpus_campaigns"
    __table_args__ = (
        CheckConstraint("release_sha ~ '^[0-9a-f]{40}$'", name="ck_qa_corpus_release_sha"),
        CheckConstraint("image_digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_qa_corpus_image_digest"),
        CheckConstraint("mode IN ('format', 'unslop', 'both')", name="ck_qa_corpus_mode"),
        CheckConstraint("destination_drive_id IS NULL", name="ck_qa_corpus_destination_my_drive"),
        CheckConstraint(
            "source_folder_id <> destination_folder_id", name="ck_qa_corpus_distinct_folders"
        ),
        CheckConstraint("document_count = 20", name="ck_qa_corpus_document_count"),
        CheckConstraint("unsupported_count = 0", name="ck_qa_corpus_unsupported_count"),
        CheckConstraint(
            "octet_length(bootstrap_capability_digest) = 32",
            name="ck_qa_corpus_capability_digest",
        ),
        CheckConstraint(
            "char_length(user_email) BETWEEN 3 AND 320 "
            "AND char_length(execution_name) BETWEEN 1 AND 1024 "
            "AND char_length(execution_job_name) BETWEEN 1 AND 1024 "
            "AND char_length(source_folder_id) BETWEEN 1 AND 255 "
            "AND (source_drive_id IS NULL OR char_length(source_drive_id) BETWEEN 1 AND 255) "
            "AND char_length(destination_folder_id) BETWEEN 1 AND 255 "
            "AND char_length(destination_version_at_plan) BETWEEN 1 AND 255",
            name="ck_qa_corpus_campaign_text_bounds",
        ),
        CheckConstraint(
            "campaign_identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND corpus_plan_sha256 ~ '^[0-9a-f]{64}$' "
            "AND source_root_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_permission_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_qa_corpus_campaign_digests",
        ),
        CheckConstraint(
            "(canary_result_sha256 IS NULL OR canary_result_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (canary_evidence_sha256 IS NULL OR canary_evidence_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (canary_output_permission_sha256 IS NULL "
            "OR canary_output_permission_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_qa_corpus_canary_digests",
        ),
        CheckConstraint(
            "state IN ('planned', 'canary_reserved', 'canary_submitted', "
            "'canary_verified', 'running', 'verified', 'failed')",
            name="ck_qa_corpus_campaign_state",
        ),
        CheckConstraint(
            "(canary_task_plan_id IS NULL AND canary_task_id IS NULL "
            "AND canary_result_sha256 IS NULL AND canary_evidence_sha256 IS NULL "
            "AND canary_output_permission_sha256 IS NULL AND canary_needs_review IS NULL "
            "AND canary_review_finding_count IS NULL AND canary_verified_at IS NULL) OR "
            "(canary_task_plan_id IS NOT NULL AND canary_task_id IS NULL "
            "AND canary_result_sha256 IS NULL AND canary_evidence_sha256 IS NULL "
            "AND canary_output_permission_sha256 IS NULL AND canary_needs_review IS NULL "
            "AND canary_review_finding_count IS NULL AND canary_verified_at IS NULL) OR "
            "(canary_task_plan_id IS NOT NULL AND canary_task_id IS NOT NULL "
            "AND canary_result_sha256 IS NULL AND canary_evidence_sha256 IS NULL "
            "AND canary_output_permission_sha256 IS NULL AND canary_needs_review IS NULL "
            "AND canary_review_finding_count IS NULL AND canary_verified_at IS NULL) OR "
            "(canary_task_plan_id IS NOT NULL AND canary_task_id IS NOT NULL "
            "AND canary_result_sha256 IS NOT NULL AND canary_evidence_sha256 IS NOT NULL "
            "AND canary_output_permission_sha256 IS NOT NULL "
            "AND canary_needs_review IS FALSE AND canary_review_finding_count = 0 "
            "AND canary_verified_at IS NOT NULL)",
            name="ck_qa_corpus_canary_attestation",
        ),
        CheckConstraint(
            "(state = 'planned' AND canary_task_plan_id IS NULL) OR "
            "(state = 'canary_reserved' AND canary_task_plan_id IS NOT NULL "
            "AND canary_task_id IS NULL) OR "
            "(state = 'canary_submitted' AND canary_task_id IS NOT NULL "
            "AND canary_result_sha256 IS NULL) OR "
            "(state IN ('canary_verified', 'running', 'verified') "
            "AND canary_result_sha256 IS NOT NULL) OR state = 'failed'",
            name="ck_qa_corpus_campaign_state_tuple",
        ),
        CheckConstraint(
            "(state = 'verified' AND completed_at IS NOT NULL AND failed_at IS NULL) OR "
            "(state = 'failed' AND failed_at IS NOT NULL AND completed_at IS NULL) OR "
            "(state NOT IN ('verified', 'failed') AND completed_at IS NULL AND failed_at IS NULL)",
            name="ck_qa_corpus_terminal_state",
        ),
        CheckConstraint(
            "(canary_verified_at IS NULL OR canary_verified_at >= created_at) "
            "AND (completed_at IS NULL OR (completed_at >= created_at "
            "AND (canary_verified_at IS NULL OR completed_at >= canary_verified_at))) "
            "AND (failed_at IS NULL OR (failed_at >= created_at "
            "AND (canary_verified_at IS NULL OR failed_at >= canary_verified_at)))",
            name="ck_qa_corpus_campaign_timestamps",
        ),
        Index("ix_qa_corpus_campaigns_created_at", "created_at"),
        Index("ix_qa_corpus_campaigns_binding", "user_id", "source_folder_id", "state"),
        Index(
            "uq_qa_corpus_campaign_active_resume",
            "user_id",
            "source_folder_id",
            "destination_folder_id",
            "mode",
            unique=True,
            postgresql_where=text("state NOT IN ('verified', 'failed')"),
        ),
        UniqueConstraint("qa_run_id", name="uq_qa_corpus_campaign_run"),
        UniqueConstraint("qa_grant_id", name="uq_qa_corpus_campaign_grant"),
        UniqueConstraint("execution_name", name="uq_qa_corpus_campaign_execution_name"),
        UniqueConstraint("execution_uid", name="uq_qa_corpus_campaign_execution_uid"),
        UniqueConstraint("bootstrap_capability_digest", name="uq_qa_corpus_campaign_capability"),
        UniqueConstraint("corpus_plan_id", name="uq_qa_corpus_campaign_plan"),
        UniqueConstraint("campaign_identity_sha256", name="uq_qa_corpus_campaign_identity"),
    )

    id: Mapped[DocumentTransformQaCorpusCampaignId] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    qa_run_id: Mapped[QaRunId] = mapped_column(UUID(as_uuid=True), nullable=False)
    qa_grant_id: Mapped[DocumentTransformQaGrantId] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    user_id: Mapped[UserId] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_email: Mapped[str] = mapped_column(Text, nullable=False)
    execution_name: Mapped[str] = mapped_column(Text, nullable=False)
    execution_uid: Mapped[DocumentTransformQaExecutionUid] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    execution_job_name: Mapped[str] = mapped_column(Text, nullable=False)
    bootstrap_capability_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    release_sha: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    source_folder_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    source_drive_id: Mapped[DriveId | None] = mapped_column(Text)
    destination_folder_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    destination_drive_id: Mapped[DriveId | None] = mapped_column(Text)
    corpus_plan_id: Mapped[QaPlanId] = mapped_column(UUID(as_uuid=True), nullable=False)
    campaign_identity_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    corpus_plan_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    source_root_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    destination_identity_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    destination_permission_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    destination_version_at_plan: Mapped[str] = mapped_column(Text, nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unsupported_count: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    canary_task_plan_id: Mapped[QaPlanId | None] = mapped_column(UUID(as_uuid=True))
    canary_task_id: Mapped[TaskId | None] = mapped_column(UUID(as_uuid=True))
    canary_result_sha256: Mapped[str | None] = mapped_column(Text)
    canary_evidence_sha256: Mapped[str | None] = mapped_column(Text)
    canary_output_permission_sha256: Mapped[str | None] = mapped_column(Text)
    canary_needs_review: Mapped[bool | None] = mapped_column(Boolean)
    canary_review_finding_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canary_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentTransformQaCorpusMember(Base):
    __tablename__ = "document_transform_qa_corpus_members"
    __table_args__ = (
        CheckConstraint("ordinal BETWEEN 0 AND 19", name="ck_qa_corpus_member_ordinal"),
        CheckConstraint(
            "mime_type IN ('application/vnd.google-apps.document', "
            "'application/vnd.openxmlformats-officedocument.wordprocessingml.document')",
            name="ck_qa_corpus_member_mime",
        ),
        CheckConstraint(
            "selected_mode IN ('format', 'unslop', 'both')", name="ck_qa_corpus_member_mode"
        ),
        CheckConstraint(
            "mime_type <> 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
            "OR selected_mode = 'format'",
            name="ck_qa_corpus_member_docx_mode",
        ),
        CheckConstraint(
            "char_length(source_file_id) BETWEEN 1 AND 255 "
            "AND char_length(source_name) BETWEEN 1 AND 1024 "
            "AND char_length(source_parent_folder_id) BETWEEN 1 AND 255 "
            "AND (source_drive_id IS NULL OR char_length(source_drive_id) BETWEEN 1 AND 255) "
            "AND char_length(source_version) BETWEEN 1 AND 255 "
            "AND char_length(source_modified_time) BETWEEN 1 AND 128",
            name="ck_qa_corpus_member_text_bounds",
        ),
        CheckConstraint(
            "source_fingerprint_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_qa_corpus_member_fingerprint",
        ),
        CheckConstraint(
            "task_plan_id = idempotency_key", name="ck_qa_corpus_member_operation_identity"
        ),
        UniqueConstraint("campaign_id", "ordinal", name="uq_qa_corpus_member_ordinal"),
        UniqueConstraint("campaign_id", "source_file_id", name="uq_qa_corpus_member_source"),
        UniqueConstraint("id", "campaign_id", name="uq_qa_corpus_member_campaign_identity"),
        UniqueConstraint("task_plan_id", name="uq_qa_corpus_member_task_plan"),
    )

    id: Mapped[DocumentTransformQaCorpusMemberId] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    campaign_id: Mapped[DocumentTransformQaCorpusCampaignId] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_transform_qa_corpus_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_file_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    source_mime_type: Mapped[str] = mapped_column("mime_type", Text, nullable=False)
    source_parent_folder_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    source_drive_id: Mapped[DriveId | None] = mapped_column(Text)
    source_version: Mapped[str] = mapped_column(Text, nullable=False)
    source_modified_time: Mapped[str] = mapped_column(Text, nullable=False)
    source_fingerprint_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    selected_mode: Mapped[str] = mapped_column(Text, nullable=False)
    task_plan_id: Mapped[QaPlanId] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[IdempotencyKey] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentTransformQaCorpusSubmission(Base):
    __tablename__ = "document_transform_qa_corpus_submissions"
    __table_args__ = (
        CheckConstraint(
            "state IN ('reserved', 'submitted', 'verified', 'failed')",
            name="ck_qa_corpus_submission_state",
        ),
        CheckConstraint(
            "task_plan_id = idempotency_key", name="ck_qa_corpus_submission_operation_identity"
        ),
        CheckConstraint(
            "source_fingerprint_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_identity_sha256 ~ '^[0-9a-f]{64}$' "
            "AND destination_permission_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_qa_corpus_submission_binding_digests",
        ),
        CheckConstraint(
            "(result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$') "
            "AND (output_permission_sha256 IS NULL "
            "OR output_permission_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_qa_corpus_submission_terminal_digests",
        ),
        ForeignKeyConstraint(
            ["member_id", "campaign_id"],
            [
                "document_transform_qa_corpus_members.id",
                "document_transform_qa_corpus_members.campaign_id",
            ],
            ondelete="CASCADE",
            name="fk_qa_corpus_submission_member_campaign",
        ),
        CheckConstraint(
            "(output_file_id IS NULL AND output_revision_id IS NULL "
            "AND result_sha256 IS NULL AND evidence_sha256 IS NULL "
            "AND output_permission_sha256 IS NULL AND needs_review IS NULL "
            "AND review_finding_count IS NULL) OR "
            "(output_file_id IS NOT NULL AND output_revision_id IS NOT NULL "
            "AND result_sha256 IS NOT NULL AND evidence_sha256 IS NOT NULL "
            "AND output_permission_sha256 IS NOT NULL AND needs_review IS NOT NULL "
            "AND review_finding_count IS NOT NULL)",
            name="ck_qa_corpus_submission_terminal_tuple",
        ),
        CheckConstraint(
            "review_finding_count IS NULL OR review_finding_count >= 0",
            name="ck_qa_corpus_submission_review_count",
        ),
        CheckConstraint(
            "(output_file_id IS NULL OR char_length(output_file_id) BETWEEN 1 AND 255) "
            "AND (output_revision_id IS NULL OR char_length(output_revision_id) BETWEEN 1 AND 255)",
            name="ck_qa_corpus_submission_text_bounds",
        ),
        CheckConstraint(
            "(state = 'reserved' AND task_id IS NULL AND submitted_at IS NULL "
            "AND output_file_id IS NULL AND verified_at IS NULL AND failed_at IS NULL) OR "
            "(state = 'submitted' AND task_id IS NOT NULL AND submitted_at IS NOT NULL "
            "AND output_file_id IS NULL AND verified_at IS NULL AND failed_at IS NULL) OR "
            "(state = 'verified' AND task_id IS NOT NULL AND submitted_at IS NOT NULL "
            "AND output_file_id IS NOT NULL AND output_revision_id IS NOT NULL "
            "AND result_sha256 IS NOT NULL AND evidence_sha256 IS NOT NULL "
            "AND output_permission_sha256 IS NOT NULL AND verified_at IS NOT NULL "
            "AND failed_at IS NULL) OR "
            "(state = 'failed' AND ((task_id IS NULL AND submitted_at IS NULL) OR "
            "(task_id IS NOT NULL AND submitted_at IS NOT NULL)) "
            "AND output_file_id IS NULL AND verified_at IS NULL AND failed_at IS NOT NULL)",
            name="ck_qa_corpus_submission_atomic_state",
        ),
        CheckConstraint(
            "(submitted_at IS NULL OR submitted_at >= reserved_at) "
            "AND (verified_at IS NULL OR (verified_at >= reserved_at "
            "AND verified_at >= submitted_at)) "
            "AND (failed_at IS NULL OR (failed_at >= reserved_at "
            "AND (submitted_at IS NULL OR failed_at >= submitted_at)))",
            name="ck_qa_corpus_submission_timestamps",
        ),
        UniqueConstraint("member_id", name="uq_qa_corpus_submission_member"),
        UniqueConstraint("task_plan_id", name="uq_qa_corpus_submission_task_plan"),
        Index(
            "uq_qa_corpus_submission_task",
            "task_id",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL"),
        ),
        Index(
            "uq_qa_corpus_submission_output",
            "campaign_id",
            "output_file_id",
            unique=True,
            postgresql_where=text("output_file_id IS NOT NULL"),
        ),
    )

    id: Mapped[DocumentTransformQaCorpusSubmissionId] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    campaign_id: Mapped[DocumentTransformQaCorpusCampaignId] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    member_id: Mapped[DocumentTransformQaCorpusMemberId] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    task_plan_id: Mapped[QaPlanId] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[IdempotencyKey] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_fingerprint_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    destination_identity_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    destination_permission_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[TaskId | None] = mapped_column(UUID(as_uuid=True))
    output_file_id: Mapped[FileId | None] = mapped_column(Text)
    output_revision_id: Mapped[DocsRevisionId | None] = mapped_column(Text)
    result_sha256: Mapped[str | None] = mapped_column(Text)
    evidence_sha256: Mapped[str | None] = mapped_column(Text)
    output_permission_sha256: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool | None] = mapped_column(Boolean)
    review_finding_count: Mapped[int | None] = mapped_column(Integer)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
