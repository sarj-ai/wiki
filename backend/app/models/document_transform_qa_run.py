from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import AuthSessionId, FileId, QaRunId, UserId
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
            "state IN ('created', 'execution_bound', 'bootstrapped')",
            name="ck_document_transform_qa_run_state",
        ),
        CheckConstraint(
            "(state = 'created' AND execution_name IS NULL "
            "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
            "AND bootstrapped_at IS NULL) OR "
            "(state = 'execution_bound' AND execution_name IS NOT NULL "
            "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
            "AND bootstrapped_at IS NULL) OR "
            "(state = 'bootstrapped' AND execution_name IS NOT NULL "
            "AND bootstrap_request_nonce IS NOT NULL AND bootstrap_capability_digest IS NOT NULL "
            "AND bootstrapped_at IS NOT NULL)",
            name="ck_document_transform_qa_run_atomic_state",
        ),
        CheckConstraint(
            "execution_name IS NULL OR (char_length(execution_name) BETWEEN 1 AND 255)",
            name="ck_document_transform_qa_run_execution_name_length",
        ),
        CheckConstraint(
            "bootstrap_capability_digest IS NULL OR octet_length(bootstrap_capability_digest) = 32",
            name="ck_document_transform_qa_run_capability_digest_length",
        ),
        Index("ix_document_transform_qa_runs_created_at", "created_at"),
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
    bootstrap_request_nonce: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    bootstrap_capability_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bootstrapped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
