import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (
        CheckConstraint("type IN ('page', 'section')", name="ck_pages_type"),
        Index("ix_pages_parent_id", "parent_id"),
        Index("ix_pages_created_by", "created_by"),
        Index("ix_pages_workspace_id", "workspace_id"),
        Index("ix_pages_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
    )
    google_drive_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    drive_folder_id: Mapped[str] = mapped_column(Text, nullable=False)
    children_folder_id: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
    )
    last_edited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id"),
    )
    icon: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(Text)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)
    cached_content: Mapped[dict | None] = mapped_column(JSONB)
    content_cached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class PagePermission(Base):
    __tablename__ = "page_permissions"
    __table_args__ = (
        CheckConstraint("permission IN ('view', 'edit')", name="ck_page_permissions_permission"),
        CheckConstraint(
            "user_id IS NOT NULL OR workspace_id IS NOT NULL",
            name="ck_page_permissions_at_least_one_grantee",
        ),
        Index("ix_page_permissions_page_id", "page_id"),
        Index("ix_page_permissions_user_id", "user_id"),
        Index("ix_page_permissions_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
    )
    permission: Mapped[str] = mapped_column(Text, nullable=False)
    inheritable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class StarredPage(Base):
    __tablename__ = "starred_pages"
    __table_args__ = (Index("ix_starred_pages_user_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id", ondelete="CASCADE"),
        primary_key=True,
    )
    starred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
