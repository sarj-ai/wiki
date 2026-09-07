from datetime import datetime
from typing import NewType
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, Text
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
            "expires_at > created_at",
            name="ck_document_transform_qa_grant_positive_lifetime",
        ),
    )

    id: Mapped[DocumentTransformQaGrantId] = mapped_column(UUID(as_uuid=True), primary_key=True)
    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    user_id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_session_id: Mapped[AuthSessionId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user_sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_session_revision: Mapped[int] = mapped_column(nullable=False)
    release_sha: Mapped[str] = mapped_column(Text, nullable=False)
    source_folder_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    destination_folder_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_by_execution: Mapped[str | None] = mapped_column(Text)
