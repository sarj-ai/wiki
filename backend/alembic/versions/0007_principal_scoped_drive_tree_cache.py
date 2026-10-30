from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "0007_user_drive_tree_cache"
down_revision: str | None = "0006_user_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_drive_tree_cache",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drive_id", sa.Text(), nullable=False),
        sa.Column("tree", sa.Text(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "refreshed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("user_id", "drive_id"),
    )
    op.create_index(
        "ix_user_drive_tree_cache_drive_id",
        "user_drive_tree_cache",
        ["drive_id"],
    )
    op.create_index(
        "ix_user_drive_tree_cache_refreshed_at",
        "user_drive_tree_cache",
        ["refreshed_at"],
    )
    # Legacy rows are safe only for the principal that produced them. Keep the
    # legacy table untouched for one rollback window while seeding attributable
    # rows into the isolated cache.
    op.execute(
        """
        INSERT INTO user_drive_tree_cache
            (user_id, drive_id, tree, refreshed_at, refreshed_by)
        SELECT refreshed_by, drive_id, tree, refreshed_at, refreshed_by
        FROM drive_tree_cache
        WHERE refreshed_by IS NOT NULL
        """
    )
    # Cache data is disposable. Empty the globally scoped compatibility cache
    # after seeding so stale cross-principal trees are not retained. The table
    # remains for the previous binary during the traffic-switch window and is
    # intentionally eligible for removal in the next schema release.
    op.execute("DELETE FROM drive_tree_cache")


def downgrade() -> None:
    op.drop_index("ix_user_drive_tree_cache_refreshed_at", table_name="user_drive_tree_cache")
    op.drop_index("ix_user_drive_tree_cache_drive_id", table_name="user_drive_tree_cache")
    op.drop_table("user_drive_tree_cache")
