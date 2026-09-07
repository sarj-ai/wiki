from datetime import datetime
from typing import NewType
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import AuthSessionId, FileId, UserId


DocumentTransformQaGrantId = NewType("DocumentTransformQaGrantId", uuid.UUID)


class DocumentTransformQaGrant(Base):
    __tablename__ = "document_transform_qa_grants"
    __table_args__ = (
        CheckConstraint(
            "release_sha ~ '^[0-9a-f]{40}$'",
            name="ck_document_transform_qa_grant_release_sha",
        ),
        CheckConstraint(
            "mode IN ('format', 'unslop', 'both')",
            name="ck_document_transform_qa_grant_mode",
        ),
        CheckConstraint(
            "source_folder_id <> destination_folder_id",
            name="ck_document_transform_qa_grant_distinct_folders",
        ),
        CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '15 minutes'",
            name="ck_document_transform_qa_grant_bounded_lifetime",
        ),
        CheckConstraint(
            "(consumed_at IS NULL AND consumed_by_execution IS NULL AND capability_digest IS NULL "
            "AND previous_capability_digest IS NULL AND consume_request_nonce IS NULL "
            "AND renewal_request_nonce IS NULL AND capability_expires_at IS NULL "
            "AND job_expires_at IS NULL) OR "
            "(consumed_at IS NOT NULL AND consumed_by_execution IS NOT NULL "
            "AND capability_digest IS NOT NULL AND consume_request_nonce IS NOT NULL "
            "AND capability_expires_at IS NOT NULL "
            "AND job_expires_at IS NOT NULL AND capability_expires_at <= job_expires_at)",
            name="ck_document_transform_qa_grant_atomic_state",
        ),
        CheckConstraint(
            "(previous_capability_digest IS NULL AND renewal_request_nonce IS NULL) OR "
            "(previous_capability_digest IS NOT NULL AND renewal_request_nonce IS NOT NULL)",
            name="ck_document_transform_qa_grant_renewal_replay_state",
        ),
        Index("ix_document_transform_qa_grants_expires_at", "expires_at"),
        Index("ix_document_transform_qa_grants_job_expires_at", "job_expires_at"),
    )

    id: Mapped[DocumentTransformQaGrantId] = mapped_column(UUID(as_uuid=True), primary_key=True)
    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    issuer_user_id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_by_execution: Mapped[str | None] = mapped_column(Text)
    capability_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    previous_capability_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    consume_request_nonce: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    renewal_request_nonce: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    capability_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
