import sqlalchemy as sa

from alembic import op


revision = "0020_qa_attestation"
down_revision = "0019_transform_qa_runs"
branch_labels = None
depends_on = None

_TABLE = "document_transform_qa_runs"
_UID_UNIQUE = "uq_document_transform_qa_runs_execution_uid"
_IDEMPOTENCY_INDEX = "uq_document_transform_qa_runs_launch_idempotency_key"
_ACTIVE_INDEX = "uq_document_transform_qa_runs_single_active_launch"
_STATE_CHECK = "ck_document_transform_qa_run_state"
_ATOMIC_CHECK = "ck_document_transform_qa_run_atomic_state"
_CLAIM_CHECK = "ck_document_transform_qa_run_launch_claim"
_DELETE_INCOMPATIBLE_SQL = (
    "DELETE FROM document_transform_qa_runs "
    "WHERE state IN ('launch_claimed', 'launch_failed', 'retired')"
)
_RETIRE_LEGACY_GRANTS_SQL = (
    "UPDATE document_transform_qa_grants SET revoked_at = COALESCE(revoked_at, CURRENT_TIMESTAMP) "
    "WHERE id IN (SELECT grant_id FROM document_transform_qa_runs)"
)
_RETIRE_LEGACY_RUNS_SQL = "UPDATE document_transform_qa_runs SET state = 'retired'"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("execution_uid", sa.UUID()))
    op.add_column(_TABLE, sa.Column("launch_idempotency_key", sa.UUID()))
    op.add_column(_TABLE, sa.Column("execution_job_name", sa.Text()))
    op.add_column(_TABLE, sa.Column("launch_claimed_at", sa.DateTime(timezone=True)))
    op.add_column(_TABLE, sa.Column("launch_lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column(_TABLE, sa.Column("launch_failed_at", sa.DateTime(timezone=True)))
    op.drop_constraint(_STATE_CHECK, _TABLE, type_="check")
    op.drop_constraint(_ATOMIC_CHECK, _TABLE, type_="check")
    # Runs created by 0019 cannot be truthfully backfilled with an immutable
    # Cloud Run execution UID or configured job identity. Preserve them as
    # audit-only rows and revoke their grants before admitting the new path.
    op.execute(_RETIRE_LEGACY_GRANTS_SQL)
    op.execute(_RETIRE_LEGACY_RUNS_SQL)
    op.create_unique_constraint(
        _UID_UNIQUE,
        _TABLE,
        ["execution_uid"],
    )
    op.create_index(
        _IDEMPOTENCY_INDEX,
        _TABLE,
        ["launch_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("launch_idempotency_key IS NOT NULL"),
    )
    op.create_index(
        _ACTIVE_INDEX,
        _TABLE,
        [sa.text("((1))")],
        unique=True,
        postgresql_where=sa.text(
            "launch_idempotency_key IS NOT NULL "
            "AND state IN ('launch_claimed', 'execution_bound', 'bootstrapped', "
            "'launch_failed')"
        ),
    )
    op.create_check_constraint(
        _STATE_CHECK,
        _TABLE,
        "state IN ('created', 'launch_claimed', 'execution_bound', 'bootstrapped', "
        "'launch_failed', 'retired')",
    )
    op.create_check_constraint(
        _ATOMIC_CHECK,
        _TABLE,
        "(state = 'created' AND execution_name IS NULL AND execution_uid IS NULL "
        "AND execution_job_name IS NULL AND launch_claimed_at IS NULL "
        "AND launch_lease_expires_at IS NULL "
        "AND launch_failed_at IS NULL "
        "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
        "AND bootstrapped_at IS NULL) OR "
        "(state = 'launch_claimed' AND execution_name IS NULL AND execution_uid IS NULL "
        "AND execution_job_name IS NULL AND launch_claimed_at IS NOT NULL "
        "AND launch_lease_expires_at IS NOT NULL "
        "AND launch_failed_at IS NULL AND bootstrap_request_nonce IS NULL "
        "AND bootstrap_capability_digest IS NULL AND bootstrapped_at IS NULL) OR "
        "(state = 'execution_bound' AND execution_name IS NOT NULL "
        "AND execution_uid IS NOT NULL AND execution_job_name IS NOT NULL "
        "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NULL "
        "AND bootstrap_request_nonce IS NULL "
        "AND bootstrap_capability_digest IS NULL AND bootstrapped_at IS NULL) OR "
        "(state = 'bootstrapped' AND execution_name IS NOT NULL "
        "AND execution_uid IS NOT NULL AND execution_job_name IS NOT NULL "
        "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NULL "
        "AND bootstrap_request_nonce IS NOT NULL "
        "AND bootstrap_capability_digest IS NOT NULL AND bootstrapped_at IS NOT NULL) OR "
        "(state = 'launch_failed' AND execution_name IS NULL AND execution_uid IS NULL "
        "AND execution_job_name IS NULL AND launch_claimed_at IS NOT NULL "
        "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NOT NULL "
        "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
        "AND bootstrapped_at IS NULL) OR "
        "(state = 'retired' AND execution_uid IS NULL AND execution_job_name IS NULL "
        "AND launch_idempotency_key IS NULL AND launch_claimed_at IS NULL "
        "AND launch_lease_expires_at IS NULL AND launch_failed_at IS NULL AND "
        "((execution_name IS NULL AND bootstrap_request_nonce IS NULL "
        "AND bootstrap_capability_digest IS NULL AND bootstrapped_at IS NULL) OR "
        "(execution_name IS NOT NULL AND bootstrap_request_nonce IS NULL "
        "AND bootstrap_capability_digest IS NULL AND bootstrapped_at IS NULL) OR "
        "(execution_name IS NOT NULL AND bootstrap_request_nonce IS NOT NULL "
        "AND bootstrap_capability_digest IS NOT NULL AND bootstrapped_at IS NOT NULL)))",
    )
    op.create_check_constraint(
        _CLAIM_CHECK,
        _TABLE,
        "(launch_idempotency_key IS NULL AND launch_claimed_at IS NULL) OR "
        "(launch_idempotency_key IS NOT NULL AND launch_claimed_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.execute(_DELETE_INCOMPATIBLE_SQL)
    op.drop_constraint(_CLAIM_CHECK, _TABLE, type_="check")
    op.drop_constraint(_ATOMIC_CHECK, _TABLE, type_="check")
    op.drop_index(_ACTIVE_INDEX)
    op.drop_index(_IDEMPOTENCY_INDEX)
    op.drop_constraint(_STATE_CHECK, _TABLE, type_="check")
    op.create_check_constraint(
        _STATE_CHECK,
        _TABLE,
        "state IN ('created', 'execution_bound', 'bootstrapped')",
    )
    op.create_check_constraint(
        _ATOMIC_CHECK,
        _TABLE,
        "(state = 'created' AND execution_name IS NULL "
        "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
        "AND bootstrapped_at IS NULL) OR "
        "(state = 'execution_bound' AND execution_name IS NOT NULL "
        "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
        "AND bootstrapped_at IS NULL) OR "
        "(state = 'bootstrapped' AND execution_name IS NOT NULL "
        "AND bootstrap_request_nonce IS NOT NULL AND bootstrap_capability_digest IS NOT NULL "
        "AND bootstrapped_at IS NOT NULL)",
    )
    op.drop_constraint(
        _UID_UNIQUE,
        _TABLE,
        type_="unique",
    )
    op.drop_column(_TABLE, "launch_failed_at")
    op.drop_column(_TABLE, "execution_job_name")
    op.drop_column(_TABLE, "launch_lease_expires_at")
    op.drop_column(_TABLE, "launch_claimed_at")
    op.drop_column(_TABLE, "execution_uid")
    op.drop_column(_TABLE, "launch_idempotency_key")
