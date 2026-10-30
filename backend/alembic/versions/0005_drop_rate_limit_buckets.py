from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0005_drop_rate_limit_buckets"
down_revision: str | None = "0004_tasks_and_rate_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("rate_limit_buckets")
    op.create_table(
        "cache_entries",
        sa.Column("namespace", sa.Text, nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("namespace", "key"),
    )
    op.create_index("ix_cache_entries_expires_at", "cache_entries", ["expires_at"])


def downgrade() -> None:
    op.drop_table("cache_entries")
    op.create_table(
        "rate_limit_buckets",
        sa.Column("principal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.Text, nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("count > 0", name="ck_rate_limit_buckets_count_positive"),
        sa.PrimaryKeyConstraint("principal_id", "operation", "window_start"),
    )
    op.create_index(
        "ix_rate_limit_buckets_expires_at",
        "rate_limit_buckets",
        ["expires_at"],
    )
