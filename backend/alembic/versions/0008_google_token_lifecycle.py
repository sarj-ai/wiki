from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "0008_google_token_lifecycle"
down_revision: str | None = "0007_user_drive_tree_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("google_access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "google_token_generation",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column("google_refresh_lease_owner", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("google_refresh_lease_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "google_refresh_lease_until")
    op.drop_column("users", "google_refresh_lease_owner")
    op.drop_column("users", "google_token_generation")
    op.drop_column("users", "google_access_token_expires_at")
