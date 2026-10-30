from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence


revision: str = "0009_transform_checkpoints"
down_revision: str | None = "0008_google_token_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_CHECKPOINT_OBJECT_CONSTRAINT = "ck_tasks_checkpoint_object"
_CHECKPOINT_GENERATION_CONSTRAINT = "ck_tasks_checkpoint_generation_nonnegative"


def upgrade() -> None:
    op.add_column("tasks", sa.Column("checkpoint", JSONB(), nullable=True))
    op.add_column("tasks", sa.Column("in_place_target", sa.Text(), nullable=True))
    op.add_column(
        "tasks",
        sa.Column(
            "checkpoint_generation",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        _CHECKPOINT_OBJECT_CONSTRAINT,
        "tasks",
        "checkpoint IS NULL OR jsonb_typeof(checkpoint) = 'object'",
    )
    op.create_index(
        "uq_tasks_active_in_place_target",
        "tasks",
        ["in_place_target"],
        unique=True,
        postgresql_where=sa.text(
            "in_place_target IS NOT NULL AND status IN ('pending', 'running')"
        ),
    )
    op.create_check_constraint(
        _CHECKPOINT_GENERATION_CONSTRAINT,
        "tasks",
        "checkpoint_generation >= 0",
    )


def downgrade() -> None:
    op.drop_index("uq_tasks_active_in_place_target", table_name="tasks")
    op.drop_constraint(_CHECKPOINT_GENERATION_CONSTRAINT, "tasks", type_="check")
    op.drop_constraint(_CHECKPOINT_OBJECT_CONSTRAINT, "tasks", type_="check")
    op.drop_column("tasks", "checkpoint_generation")
    op.drop_column("tasks", "checkpoint")
    op.drop_column("tasks", "in_place_target")
