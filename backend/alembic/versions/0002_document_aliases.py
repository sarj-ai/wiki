from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002_document_aliases"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_aliases",
        sa.Column("alias", sa.Text, primary_key=True),
        sa.Column("file_id", sa.Text, nullable=False),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "retired_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("retired_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(alias) <= 32 AND alias ~ '^[a-z]{2,12}(-[a-z]{2,12}){1,2}$'",
            name="ck_document_aliases_format",
        ),
    )
    op.create_index(
        "uq_document_aliases_active_file",
        "document_aliases",
        ["file_id"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_index(
        "ix_document_aliases_created_by",
        "document_aliases",
        ["created_by"],
        postgresql_where=sa.text("created_by IS NOT NULL"),
    )
    op.create_index(
        "ix_document_aliases_retired_by",
        "document_aliases",
        ["retired_by"],
        postgresql_where=sa.text("retired_by IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("document_aliases")
