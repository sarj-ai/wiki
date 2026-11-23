from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


if TYPE_CHECKING:
    from datetime import datetime


revision = "0031_model_batches"
down_revision = "0030_native_oauth_invariants"
branch_labels = None
depends_on = None


OLD_CHECKS: Final[dict[str, str]] = {
    "ck_tasks_status": (
        "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'needs_review')"
    ),
    "ck_tasks_attempt_count_matches_state": (
        "(status = 'pending' AND attempt_count = 0) OR (status = 'cancelled' "
        "AND attempt_count = 0) OR status IN ('running', 'succeeded', 'failed',"
        " 'needs_review') AND attempt_count > 0"
    ),
    "ck_tasks_legal_state": (
        "(status = 'pending' AND started_at IS NULL AND finished_at IS NULL AND"
        " lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
        "AND error_code IS NULL AND error_detail IS NULL) OR (status = "
        "'running' AND started_at IS NOT NULL AND finished_at IS NULL AND "
        "lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND result IS"
        " NULL AND error_code IS NULL AND error_detail IS NULL) OR (status = "
        "'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND"
        " lease_token IS NULL AND lease_expires_at IS NULL AND result IS NOT "
        "NULL AND error_code IS NULL AND error_detail IS NULL) OR (status = "
        "'failed' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND "
        "lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
        "AND error_code IS NOT NULL) OR (status = 'cancelled' AND started_at IS"
        " NULL AND finished_at IS NOT NULL AND lease_token IS NULL AND "
        "lease_expires_at IS NULL AND result IS NULL AND error_code IS NULL AND"
        " error_detail IS NULL) OR (status = 'needs_review' AND started_at IS "
        "NOT NULL AND finished_at IS NOT NULL AND lease_token IS NULL AND "
        "lease_expires_at IS NULL AND result IS NULL AND error_code IS NOT NULL"
        " AND error_code = 'external_effect_uncertain' AND error_detail IS NOT "
        "NULL AND char_length(error_detail) > 0)"
    ),
}

NEW_CHECKS: Final[dict[str, str]] = {
    "ck_tasks_status": (
        "status IN ('pending', 'running', 'awaiting_model', 'model_ready', "
        "'succeeded', 'failed', 'cancelled', 'needs_review')"
    ),
    "ck_tasks_attempt_count_matches_state": (
        "(status = 'pending' AND attempt_count = 0) OR (status = 'cancelled' "
        "AND attempt_count = 0) OR status IN ('running', 'awaiting_model', "
        "'model_ready', 'succeeded', 'failed', 'needs_review') AND "
        "attempt_count > 0"
    ),
    "ck_tasks_legal_state": (
        "(status = 'pending' AND started_at IS NULL AND finished_at IS NULL AND"
        " lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
        "AND error_code IS NULL AND error_detail IS NULL) OR (status = "
        "'running' AND started_at IS NOT NULL AND finished_at IS NULL AND "
        "lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND result IS"
        " NULL AND error_code IS NULL AND error_detail IS NULL) OR (status IN "
        "('awaiting_model', 'model_ready') AND kind = 'file.document_transform'"
        " AND started_at IS NOT NULL AND finished_at IS NULL AND lease_token IS"
        " NULL AND lease_expires_at IS NULL AND result IS NULL AND error_code "
        "IS NULL AND error_detail IS NULL AND (status = 'awaiting_model' OR "
        "dispatch_ready_at IS NOT NULL)) OR (status = 'succeeded' AND "
        "started_at IS NOT NULL AND finished_at IS NOT NULL AND lease_token IS "
        "NULL AND lease_expires_at IS NULL AND result IS NOT NULL AND "
        "error_code IS NULL AND error_detail IS NULL) OR (status = 'failed' AND"
        " started_at IS NOT NULL AND finished_at IS NOT NULL AND lease_token IS"
        " NULL AND lease_expires_at IS NULL AND result IS NULL AND error_code "
        "IS NOT NULL) OR (status = 'cancelled' AND started_at IS NULL AND "
        "finished_at IS NOT NULL AND lease_token IS NULL AND lease_expires_at "
        "IS NULL AND result IS NULL AND error_code IS NULL AND error_detail IS "
        "NULL) OR (status = 'needs_review' AND started_at IS NOT NULL AND "
        "finished_at IS NOT NULL AND lease_token IS NULL AND lease_expires_at "
        "IS NULL AND result IS NULL AND error_code IS NOT NULL AND error_code ="
        " 'external_effect_uncertain' AND error_detail IS NOT NULL AND "
        "char_length(error_detail) > 0)"
    ),
}

OLD_ACTIVE_IN_PLACE = (
    "in_place_target IS NOT NULL AND status IN ('pending', 'running', 'needs_review')"
)
NEW_ACTIVE_IN_PLACE = (
    "in_place_target IS NOT NULL "
    "AND status IN ('pending', 'running', 'awaiting_model', 'model_ready', 'needs_review')"
)
_JOB_STATES = (
    "(status IN ('prepared', 'submitting') AND vertex_job_name IS NULL "
    "AND provider_state IS NULL) OR "
    "(status = 'running' AND vertex_job_name IS NOT NULL AND provider_state IS NULL) OR "
    "(status = 'ingested' AND vertex_job_name IS NOT NULL "
    "AND provider_state IN ('succeeded', 'partially_succeeded')) OR "
    "(status = 'failed' AND provider_state IN ('failed', 'cancelled', 'expired', "
    "'rejected') AND (provider_state = 'rejected') = (vertex_job_name IS NULL))"
)
_CALL_STATES = (
    "(status = 'pending' AND job_id IS NULL AND outcome_ciphertext IS NULL) OR "
    "(status = 'queued' AND job_id IS NOT NULL AND outcome_ciphertext IS NULL) OR "
    "(status IN ('succeeded', 'failed') AND job_id IS NOT NULL "
    "AND outcome_ciphertext IS NOT NULL)"
)


def _replace_task_checks(drop: dict[str, str], create: dict[str, str], where: str) -> None:
    for name in drop:
        op.drop_constraint(name, "tasks", type_="check")
    for name, expression in create.items():
        op.create_check_constraint(name, "tasks", expression)
    op.drop_index("uq_tasks_active_in_place_target", table_name="tasks")
    op.create_index(
        "uq_tasks_active_in_place_target",
        "tasks",
        ["in_place_target"],
        unique=True,
        postgresql_where=sa.text(where),
    )


def upgrade() -> None:
    op.execute("LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE")
    _replace_task_checks(OLD_CHECKS, NEW_CHECKS, NEW_ACTIVE_IN_PLACE)
    op.create_table(
        "model_batch_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("vertex_job_name", sa.Text(), nullable=True),
        sa.Column("provider_state", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('prepared', 'submitting', 'running', 'ingested', 'failed')",
            name="ck_model_batch_jobs_status",
        ),
        sa.CheckConstraint(_JOB_STATES, name="ck_model_batch_jobs_legal_state"),
    )
    op.create_index("ix_model_batch_jobs_task", "model_batch_jobs", ["task_id"])
    op.create_index(
        "ix_model_batch_jobs_active",
        "model_batch_jobs",
        ["updated_at", "id"],
        postgresql_where=sa.text("status IN ('prepared', 'submitting', 'running')"),
    )
    op.create_table(
        "model_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_sha256", sa.Text(), nullable=False),
        sa.Column("occurrence", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_batch_jobs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("request_ciphertext", sa.Text(), nullable=False),
        sa.Column("outcome_ciphertext", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('pending', 'queued', 'succeeded', 'failed')",
            name="ck_model_calls_status",
        ),
        sa.CheckConstraint("char_length(request_sha256) = 64", name="ck_model_calls_request_hash"),
        sa.CheckConstraint("occurrence > 0", name="ck_model_calls_occurrence_positive"),
        sa.CheckConstraint(_CALL_STATES, name="ck_model_calls_legal_state"),
        sa.UniqueConstraint(
            "task_id", "request_sha256", "occurrence", name="uq_model_calls_task_request"
        ),
    )
    op.create_index("ix_model_calls_job", "model_calls", ["job_id"])


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def downgrade() -> None:
    op.execute("LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tasks WHERE status IN ('awaiting_model', 'model_ready')) THEN "
        "RAISE EXCEPTION 'Drain tasks awaiting model batches before downgrading'; "
        "END IF; END; $$;"
    )
    op.drop_index("ix_model_calls_job", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index("ix_model_batch_jobs_active", table_name="model_batch_jobs")
    op.drop_index("ix_model_batch_jobs_task", table_name="model_batch_jobs")
    op.drop_table("model_batch_jobs")
    _replace_task_checks(NEW_CHECKS, OLD_CHECKS, OLD_ACTIVE_IN_PLACE)
