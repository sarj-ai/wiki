from datetime import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import AuthSessionId, FileId, QaPlanId, QaRunId, TaskId, UserId
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId


class DocumentTransformQaRun(Base):
    __tablename__ = "document_transform_qa_runs"
    __table_args__ = (
        CheckConstraint(
            "release_sha ~ '^[0-9a-f]{40}$'",
            name="ck_document_transform_qa_run_release_sha",
        ),
        CheckConstraint(
            "image_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_document_transform_qa_run_image_digest",
        ),
        CheckConstraint(
            "mode IN ('format', 'unslop', 'both')",
            name="ck_document_transform_qa_run_mode",
        ),
        CheckConstraint(
            "source_folder_id <> destination_folder_id",
            name="ck_document_transform_qa_run_distinct_folders",
        ),
        CheckConstraint(
            "state IN ('created', 'launch_claimed', 'execution_bound', 'bootstrapped', "
            "'launch_failed', 'retired')",
            name="ck_document_transform_qa_run_state",
        ),
        CheckConstraint(
            "(state = 'created' AND execution_name IS NULL AND execution_uid IS NULL "
            "AND execution_job_name IS NULL AND launch_claimed_at IS NULL "
            "AND launch_lease_expires_at IS NULL "
            "AND launch_failed_at IS NULL "
            "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
            "AND bootstrapped_at IS NULL) OR "
            "(state = 'launch_claimed' AND execution_name IS NULL AND execution_uid IS NULL "
            "AND execution_job_name IS NULL AND launch_claimed_at IS NOT NULL "
            "AND launch_lease_expires_at IS NOT NULL "
            "AND launch_failed_at IS NULL AND bootstrap_request_nonce IS NULL "
            "AND bootstrap_capability_digest IS NULL AND bootstrapped_at IS NULL) OR "
            "(state = 'execution_bound' AND execution_name IS NOT NULL "
            "AND execution_uid IS NOT NULL AND execution_job_name IS NOT NULL "
            "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NULL "
            "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
            "AND bootstrapped_at IS NULL) OR "
            "(state = 'bootstrapped' AND execution_name IS NOT NULL "
            "AND execution_uid IS NOT NULL AND execution_job_name IS NOT NULL "
            "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NULL "
            "AND bootstrap_request_nonce IS NOT NULL AND bootstrap_capability_digest IS NOT NULL "
            "AND bootstrapped_at IS NOT NULL) OR "
            "(state = 'launch_failed' AND execution_name IS NULL AND execution_uid IS NULL "
            "AND execution_job_name IS NULL AND launch_claimed_at IS NOT NULL "
            "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NOT NULL "
            "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
            "AND bootstrapped_at IS NULL) OR "
            "(state = 'retired' AND execution_uid IS NULL AND execution_job_name IS NULL "
            "AND launch_idempotency_key IS NULL AND launch_claimed_at IS NULL "
            "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NULL AND "
            "((execution_name IS NULL AND bootstrap_request_nonce IS NULL "
            "AND bootstrap_capability_digest IS NULL AND bootstrapped_at IS NULL) OR "
            "(execution_name IS NOT NULL AND bootstrap_request_nonce IS NULL "
            "AND bootstrap_capability_digest IS NULL AND bootstrapped_at IS NULL) OR "
            "(execution_name IS NOT NULL AND bootstrap_request_nonce IS NOT NULL "
            "AND bootstrap_capability_digest IS NOT NULL AND bootstrapped_at IS NOT NULL)))",
            name="ck_document_transform_qa_run_atomic_state",
        ),
        CheckConstraint(
            "(launch_idempotency_key IS NULL AND launch_claimed_at IS NULL) OR "
            "(launch_idempotency_key IS NOT NULL AND launch_claimed_at IS NOT NULL)",
            name="ck_document_transform_qa_run_launch_claim",
        ),
        CheckConstraint(
            "execution_name IS NULL OR (char_length(execution_name) BETWEEN 1 AND 255)",
            name="ck_document_transform_qa_run_execution_name_length",
        ),
        CheckConstraint(
            "bootstrap_capability_digest IS NULL OR octet_length(bootstrap_capability_digest) = 32",
            name="ck_document_transform_qa_run_capability_digest_length",
        ),
        CheckConstraint(
            "(submitted_plan_id IS NULL AND submitted_task_id IS NULL AND submitted_at IS NULL) OR "
            "(state = 'bootstrapped' AND submitted_plan_id IS NOT NULL "
            "AND submitted_task_id IS NOT NULL AND submitted_at IS NOT NULL)",
            name="ck_document_transform_qa_run_submission_atomic",
        ),
        UniqueConstraint(
            "submitted_plan_id",
            name="uq_document_transform_qa_runs_submitted_plan_id",
        ),
        Index("ix_document_transform_qa_runs_created_at", "created_at"),
        Index(
            "uq_document_transform_qa_runs_launch_idempotency_key",
            "launch_idempotency_key",
            unique=True,
            postgresql_where=text("launch_idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_document_transform_qa_runs_single_active_launch",
            text("((1))"),
            unique=True,
            postgresql_where=text(
                "launch_idempotency_key IS NOT NULL "
                "AND state IN ('launch_claimed', 'execution_bound', 'bootstrapped', "
                "'launch_failed')"
            ),
        ),
    )

    id: Mapped[QaRunId] = mapped_column(UUID(as_uuid=True), primary_key=True)
    grant_id: Mapped[DocumentTransformQaGrantId] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_transform_qa_grants.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    user_id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_session_id: Mapped[AuthSessionId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_session_revision: Mapped[int] = mapped_column(nullable=False)
    release_sha: Mapped[str] = mapped_column(Text, nullable=False)
    image_digest: Mapped[str] = mapped_column(Text, nullable=False)
    source_folder_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    destination_folder_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    execution_name: Mapped[str | None] = mapped_column(Text, unique=True)
    launch_idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    execution_uid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    execution_job_name: Mapped[str | None] = mapped_column(Text)
    launch_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launch_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    launch_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bootstrap_request_nonce: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    bootstrap_capability_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bootstrapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_plan_id: Mapped[QaPlanId | None] = mapped_column(UUID(as_uuid=True))
    # Historical identity snapshot, deliberately not a task FK: task retention
    # must be able to expire the submitted task without erasing QA audit state.
    submitted_task_id: Mapped[TaskId | None] = mapped_column(UUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
