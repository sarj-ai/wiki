"""SQLAlchemy models for the Navigator pivot's new tables.

See PIVOT_PLAN.md §6.1 for the data model. These are pure-overlay
tables on top of live Drive state — nothing here is authoritative,
and all of it can be dropped and rebuilt from Drive if needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Double, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DriveTreeCache(Base):
    """Shared per-drive tree cache. One row per Google shared drive.

    Every member of the drive sees the same tree, so we cache it once
    and amortize the cold-walk cost across all users. Access control
    happens at sidebar load via ``drives.list()``; a non-member simply
    never queries this row even though it exists.
    """

    __tablename__ = "drive_tree_cache"

    drive_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tree: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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
    """Global, editable icon + cover for a single Google Doc.

    Any user with Drive write access to the doc can edit the icon; the
    change is visible to everyone. Folders do NOT use this table — see
    ``UserFolderIcon`` below.
    """

    __tablename__ = "doc_icons"

    drive_id: Mapped[str] = mapped_column(Text, primary_key=True)
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


class UserFolderIcon(Base):
    """Per-user folder icon override.

    User A sees folder X with a rocket; User B sees the same folder
    with a wrench. Stored as ``(user_id, folder_drive_id)`` so the same
    folder can have a distinct icon per user.
    """

    __tablename__ = "user_folder_icons"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    folder_drive_id: Mapped[str] = mapped_column(Text, primary_key=True)
    icon: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class UserSortOrder(Base):
    """Per-user drag-drop ordering via fractional index.

    Each user reorders their sidebar independently (decision 11.1).
    ``drive_id`` here is overloaded: it can be a file id OR a folder
    id — whatever the user is positioning inside its parent.
    """

    __tablename__ = "user_sort_order"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    drive_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sort_order: Mapped[float] = mapped_column(Double, nullable=False)


class UserPreferences(Base):
    """Per-user UI toggles for the Navigator sidebar.

    One row per user, created lazily on first write. Reads for users
    without a row fall back to the default values in code.
    """

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
    hidden_workspace_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
