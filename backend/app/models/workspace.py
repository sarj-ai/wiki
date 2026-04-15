import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    google_group_email: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    drive_folder_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class WorkspaceGroupAccess(Base):
    """Tracks which Google Groups have access to a workspace's Drive folder.

    Discovered by scanning Drive folder permissions. This allows a workspace
    folder to be shared with multiple groups beyond the primary group.
    """

    __tablename__ = "workspace_group_access"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    google_group_email: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
    )


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )


class WorkspaceUserSettings(Base):
    """Per-user settings for a workspace (e.g. custom icon)."""

    __tablename__ = "workspace_user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0"))
