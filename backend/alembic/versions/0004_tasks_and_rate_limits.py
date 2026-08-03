from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0004_tasks_and_rate_limits"
down_revision: str | None = "0003_seed_org_chart_alias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "owner_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("idempotency_key", UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("resource_id", sa.Text, nullable=True),
        sa.Column("error_code", sa.Text, nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("lease_token", UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_tasks_status",
        ),
        sa.CheckConstraint("char_length(request_hash) = 64", name="ck_tasks_request_hash"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_tasks_payload_object"),
        sa.CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'",
            name="ck_tasks_result_object",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_tasks_attempt_count_nonnegative"),
        sa.CheckConstraint(
            "(status = 'pending' AND attempt_count = 0) OR "
            "(status = 'cancelled' AND attempt_count = 0) OR "
            "status IN ('running', 'succeeded', 'failed') AND attempt_count > 0",
            name="ck_tasks_attempt_count_matches_state",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_tasks_expiry_after_creation"),
        sa.CheckConstraint(
            "lease_expires_at IS NULL OR lease_expires_at > started_at",
            name="ck_tasks_lease_after_start",
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_tasks_finish_after_start",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND finished_at IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND result IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NOT NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(status = 'failed' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
            "AND error_code IS NOT NULL) OR "
            "(status = 'cancelled' AND started_at IS NULL AND finished_at IS NOT NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_tasks_legal_state",
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "kind",
            "idempotency_key",
            name="uq_tasks_owner_kind_idempotency",
        ),
    )
    op.create_index("ix_tasks_owner_created", "tasks", ["owner_user_id", "created_at"])
    op.create_index("ix_tasks_status_lease", "tasks", ["status", "lease_expires_at"])

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


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
    op.drop_table("tasks")
