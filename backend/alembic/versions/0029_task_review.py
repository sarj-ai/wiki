from typing import Final

import sqlalchemy as sa

from alembic import op


revision = "0029_task_review"
down_revision = "0028_task_result_invariants"
branch_labels = None
depends_on = None


OLD_CHECKS: Final[dict[str, str]] = {
    "ck_tasks_status": "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
    "ck_tasks_attempt_count_matches_state": "(status = 'pending' AND attempt_count = 0) OR "
    "(status = 'cancelled' AND attempt_count = 0) OR "
    "status IN ('running', 'succeeded', 'failed') AND "
    "attempt_count > 0",
    "ck_tasks_legal_state": "(status = 'pending' AND started_at IS NULL AND finished_at IS NULL "
    "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS "
    "NULL AND error_code IS NULL AND error_detail IS NULL) OR (status = "
    "'running' AND started_at IS NOT NULL AND finished_at IS NULL AND "
    "lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND result "
    "IS NULL AND error_code IS NULL AND error_detail IS NULL) OR (status "
    "= 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
    "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS "
    "NOT NULL AND error_code IS NULL AND error_detail IS NULL) OR (status "
    "= 'failed' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
    "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS "
    "NULL AND error_code IS NOT NULL) OR (status = 'cancelled' AND "
    "started_at IS NULL AND finished_at IS NOT NULL AND lease_token IS "
    "NULL AND lease_expires_at IS NULL AND result IS NULL AND error_code "
    "IS NULL AND error_detail IS NULL)",
}

NEW_CHECKS: Final[dict[str, str]] = {
    "ck_tasks_status": "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', "
    "'needs_review')",
    "ck_tasks_attempt_count_matches_state": "(status = 'pending' AND attempt_count = 0) OR "
    "(status = 'cancelled' AND attempt_count = 0) OR "
    "status IN ('running', 'succeeded', 'failed', "
    "'needs_review') AND attempt_count > 0",
    "ck_tasks_legal_state": "(status = 'pending' AND started_at IS NULL AND finished_at IS NULL "
    "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS "
    "NULL AND error_code IS NULL AND error_detail IS NULL) OR (status = "
    "'running' AND started_at IS NOT NULL AND finished_at IS NULL AND "
    "lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND result "
    "IS NULL AND error_code IS NULL AND error_detail IS NULL) OR (status "
    "= 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
    "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS "
    "NOT NULL AND error_code IS NULL AND error_detail IS NULL) OR (status "
    "= 'failed' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
    "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS "
    "NULL AND error_code IS NOT NULL) OR (status = 'cancelled' AND "
    "started_at IS NULL AND finished_at IS NOT NULL AND lease_token IS "
    "NULL AND lease_expires_at IS NULL AND result IS NULL AND error_code "
    "IS NULL AND error_detail IS NULL) OR (status = 'needs_review' AND "
    "started_at IS NOT NULL AND finished_at IS NOT NULL AND lease_token "
    "IS NULL AND lease_expires_at IS NULL AND result IS NULL AND "
    "error_code IS NOT NULL AND error_code = 'external_effect_uncertain' "
    "AND error_detail IS NOT NULL AND char_length(error_detail) > 0)",
    "ck_tasks_review_evidence": "status != 'needs_review' OR payload <> '{\"redacted\": "
    "true}'::jsonb",
}


def upgrade() -> None:
    op.execute("LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tasks WHERE status = 'running' "
        "AND lease_expires_at > CURRENT_TIMESTAMP) THEN "
        "RAISE EXCEPTION 'Drain running executions before migrating task review state'; "
        "END IF; END; $$;"
    )
    for name in OLD_CHECKS:
        op.drop_constraint(name, "tasks", type_="check")
    for name, expression in NEW_CHECKS.items():
        op.create_check_constraint(name, "tasks", expression)
    op.drop_index("uq_tasks_active_in_place_target", table_name="tasks")
    op.create_index(
        "uq_tasks_active_in_place_target",
        "tasks",
        ["in_place_target"],
        unique=True,
        postgresql_where=sa.text(
            "in_place_target IS NOT NULL AND status IN ('pending', 'running', 'needs_review')"
        ),
    )
    op.execute(
        "UPDATE tasks SET status = 'needs_review', error_code = 'external_effect_uncertain', "
        "error_detail = 'The external action could not be confirmed; review it before retrying', "
        "lease_token = NULL, lease_expires_at = NULL, finished_at = CURRENT_TIMESTAMP, "
        "updated_at = CURRENT_TIMESTAMP WHERE "
        "status = 'running' AND lease_expires_at <= CURRENT_TIMESTAMP"
    )


def downgrade() -> None:
    op.execute("LOCK TABLE tasks IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tasks WHERE status = 'needs_review') THEN "
        "RAISE EXCEPTION 'Resolve review records explicitly before downgrading'; "
        "END IF; END; $$;"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tasks WHERE status = 'running') THEN "
        "RAISE EXCEPTION 'Drain running executions before downgrading task review state'; "
        "END IF; END; $$;"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tasks "
        "WHERE checkpoint ->> 'kind' = 'presentation_copy') THEN "
        "RAISE EXCEPTION 'Retire presentation checkpoints before downgrading'; "
        "END IF; END; $$;"
    )
    op.drop_index("uq_tasks_active_in_place_target", table_name="tasks")
    op.create_index(
        "uq_tasks_active_in_place_target",
        "tasks",
        ["in_place_target"],
        unique=True,
        postgresql_where=sa.text(
            "in_place_target IS NOT NULL AND status IN ('pending', 'running')"
        ),
    )
    for name in NEW_CHECKS:
        op.drop_constraint(name, "tasks", type_="check")
    for name, expression in OLD_CHECKS.items():
        op.create_check_constraint(name, "tasks", expression)
