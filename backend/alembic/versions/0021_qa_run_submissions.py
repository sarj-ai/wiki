import sqlalchemy as sa

from alembic import op


revision = "0021_qa_run_submissions"
down_revision = "0020_qa_attestation"
branch_labels = None
depends_on = None

_ATOMIC_CONSTRAINT = "ck_document_transform_qa_run_submission_atomic"
_PLAN_CONSTRAINT = "uq_document_transform_qa_runs_submitted_plan_id"


def upgrade() -> None:
    op.add_column("document_transform_qa_runs", sa.Column("submitted_plan_id", sa.UUID()))
    op.add_column(
        "document_transform_qa_runs",
        # Snapshot-only audit identity. Tasks have a bounded retention window,
        # while the QA run/grant lifecycle is independent.
        sa.Column("submitted_task_id", sa.UUID()),
    )
    op.add_column(
        "document_transform_qa_runs",
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
    )
    op.create_unique_constraint(
        _PLAN_CONSTRAINT,
        "document_transform_qa_runs",
        ["submitted_plan_id"],
    )
    op.create_check_constraint(
        _ATOMIC_CONSTRAINT,
        "document_transform_qa_runs",
        "(submitted_plan_id IS NULL AND submitted_task_id IS NULL AND submitted_at IS NULL) OR "
        "(state = 'bootstrapped' AND submitted_plan_id IS NOT NULL "
        "AND submitted_task_id IS NOT NULL AND submitted_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        _ATOMIC_CONSTRAINT,
        "document_transform_qa_runs",
        type_="check",
    )
    op.drop_constraint(
        _PLAN_CONSTRAINT,
        "document_transform_qa_runs",
        type_="unique",
    )
    op.drop_column("document_transform_qa_runs", "submitted_at")
    op.drop_column("document_transform_qa_runs", "submitted_task_id")
    op.drop_column("document_transform_qa_runs", "submitted_plan_id")
