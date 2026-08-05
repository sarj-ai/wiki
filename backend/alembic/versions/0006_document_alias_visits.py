from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0006_document_alias_visits"
down_revision: str | None = "0005_drop_rate_limit_buckets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_alias_visits",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_id", sa.Text, nullable=False),
        sa.Column(
            "visited_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", "file_id"),
    )
    op.create_index(
        "ix_document_alias_visits_user_visited",
        "document_alias_visits",
        ["user_id", "visited_at"],
    )


def downgrade() -> None:
    op.drop_table("document_alias_visits")
