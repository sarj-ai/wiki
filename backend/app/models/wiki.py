from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, Double, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import DriveId, FileId, TaskId, UserId


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    __table_args__ = (
        Index("ix_cache_entries_expires_at", "expires_at"),
        Index(
            "ix_cache_entries_legacy_namespace",
            "namespace",
            postgresql_where=text(r"key !~ '^h[0-9]+\.'"),
        ),
    )

    namespace: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[object] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DocumentHeaderProvenance(Base):
    __tablename__ = "document_header_provenance"

    owner_user_id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[FileId] = mapped_column(Text, primary_key=True)
    # Deliberately no task FK: historical ownership survives task retention.
    producer_operation_id: Mapped[TaskId] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    verified_revision_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DriveTreeCache(Base):
    __tablename__ = "user_drive_tree_cache"
    __table_args__ = (
        Index("ix_user_drive_tree_cache_drive_id", "drive_id"),
        Index("ix_user_drive_tree_cache_refreshed_at", "refreshed_at"),
    )

    user_id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    drive_id: Mapped[DriveId] = mapped_column(Text, primary_key=True)
    tree: Mapped[str] = mapped_column(Text, nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    refreshed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class DocIcon(Base):
    __tablename__ = "doc_icons"

    drive_id: Mapped[FileId] = mapped_column(Text, primary_key=True)
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class DocumentAlias(Base):
    __tablename__ = "document_aliases"
    __table_args__ = (
        CheckConstraint(
            "char_length(alias) <= 32 AND alias ~ '^[a-z]{2,12}(-[a-z]{2,12}){1,2}$'",
            name="ck_document_aliases_format",
        ),
        Index(
            "uq_document_aliases_active_file",
            "file_id",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
        Index(
            "ix_document_aliases_created_by",
            "created_by",
            postgresql_where=text("created_by IS NOT NULL"),
        ),
        Index(
            "ix_document_aliases_retired_by",
            "retired_by",
            postgresql_where=text("retired_by IS NOT NULL"),
        ),
    )

    alias: Mapped[str] = mapped_column(Text, primary_key=True)
    file_id: Mapped[FileId] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    retired_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserFolderIcon(Base):
    __tablename__ = "user_folder_icons"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    folder_drive_id: Mapped[FileId] = mapped_column(Text, primary_key=True)
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class UserSortOrder(Base):
    __tablename__ = "user_sort_order"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    drive_id: Mapped[DriveId] = mapped_column(Text, primary_key=True)
    sort_order: Mapped[float] = mapped_column(Double, nullable=False)


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    show_non_doc_files: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    hidden_workspace_ids: Mapped[list[DriveId]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
