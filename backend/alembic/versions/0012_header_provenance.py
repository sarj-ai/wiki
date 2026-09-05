from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "0012_header_provenance"
down_revision: str | None = "0011_transform_replay_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_header_provenance",
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("file_id", sa.Text(), primary_key=True),
        sa.Column("producer_operation_id", sa.UUID(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("result_sha256", sa.Text(), nullable=False),
        sa.Column("verified_revision_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("document_header_provenance")
