from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "0011_transform_replay_keys"
down_revision: str | None = "0010_transform_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_replay_keys",
        sa.Column(
            "owner_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.Text(), primary_key=True),
        sa.Column("idempotency_key", sa.UUID(), primary_key=True),
        sa.Column(
            "task_id", sa.UUID(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
    )
    op.create_index("ix_task_replay_keys_task_id", "task_replay_keys", ["task_id"])


def downgrade() -> None:
    op.drop_table("task_replay_keys")
