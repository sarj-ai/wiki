"""Navigator pivot: new tables

Adds the tables the Navigator backend needs (see PIVOT_PLAN.md §6.1):

- drive_tree_cache: shared per-drive JSONB tree cache
- doc_icons: global Google-Doc icons
- user_folder_icons: per-user folder icons
- user_sort_order: per-user drag-drop ordering
- user_preferences: hide-non-doc / hidden-workspace prefs

This migration is non-destructive — old tables stay alive until the
Sub-PR C drop migration runs. Lets the new Navigator code coexist with
old Sarj Docs code on the feature branch until we delete the old code.

Revision ID: e9f1a2b3c4d5
Revises: d7e8f9a0b1c2
Create Date: 2026-04-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "e9f1a2b3c4d5"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # drive_tree_cache — one row per shared drive, shared by all members.
    op.create_table(
        "drive_tree_cache",
        sa.Column("drive_id", sa.Text, primary_key=True),
        sa.Column("tree", JSONB, nullable=False),
        sa.Column(
            "refreshed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "refreshed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_drive_tree_cache_refreshed_at",
        "drive_tree_cache",
        ["refreshed_at"],
    )

    # doc_icons — global, editable by any user with Drive write on the doc.
    op.create_table(
        "doc_icons",
        sa.Column("drive_id", sa.Text, primary_key=True),
        sa.Column("icon", sa.Text, nullable=True),
        sa.Column("cover_url", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # user_folder_icons — per-user overrides for folder icons.
    op.create_table(
        "user_folder_icons",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("folder_drive_id", sa.Text, nullable=False),
        sa.Column("icon", sa.Text, nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "folder_drive_id"),
    )

    # user_sort_order — per-user drag-drop ordering via fractional index.
    op.create_table(
        "user_sort_order",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drive_id", sa.Text, nullable=False),
        sa.Column("sort_order", sa.Double, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "drive_id"),
    )

    # user_preferences — one row per user, sidebar display prefs.
    op.create_table(
        "user_preferences",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "show_non_doc_files",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "hidden_workspace_ids",
            ARRAY(sa.Text),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_preferences")
    op.drop_table("user_sort_order")
    op.drop_table("user_folder_icons")
    op.drop_table("doc_icons")
    op.drop_index("ix_drive_tree_cache_refreshed_at", table_name="drive_tree_cache")
    op.drop_table("drive_tree_cache")
