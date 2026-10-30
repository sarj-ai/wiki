from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "0010_transform_dispatch"
down_revision: str | None = "0009_transform_checkpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows are deliberately not backfilled: old pending reservations
    # may have been published before provider admission validation completed.
    op.add_column(
        "tasks", sa.Column("dispatch_ready_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tasks", "dispatch_ready_at")
