import sqlalchemy as sa

from alembic import op


revision = "0019_transform_qa_runs"
down_revision = "0018_transform_qa_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_transform_qa_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "grant_id",
            sa.UUID(),
            sa.ForeignKey("document_transform_qa_grants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_session_id",
            sa.UUID(),
            sa.ForeignKey("user_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_session_revision", sa.Integer(), nullable=False),
        sa.Column("release_sha", sa.Text(), nullable=False),
        sa.Column("image_digest", sa.Text(), nullable=False),
        sa.Column("source_folder_id", sa.Text(), nullable=False),
        sa.Column("destination_folder_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("execution_name", sa.Text(), unique=True),
        sa.Column("bootstrap_request_nonce", sa.UUID()),
        sa.Column("bootstrap_capability_digest", sa.LargeBinary(length=32), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bootstrapped_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "release_sha ~ '^[0-9a-f]{40}$'",
            name="ck_document_transform_qa_run_release_sha",
        ),
        sa.CheckConstraint(
            "image_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_document_transform_qa_run_image_digest",
        ),
        sa.CheckConstraint(
            "mode IN ('format', 'unslop', 'both')",
            name="ck_document_transform_qa_run_mode",
        ),
        sa.CheckConstraint(
            "source_folder_id <> destination_folder_id",
            name="ck_document_transform_qa_run_distinct_folders",
        ),
        sa.CheckConstraint(
            "state IN ('created', 'execution_bound', 'bootstrapped')",
            name="ck_document_transform_qa_run_state",
        ),
        sa.CheckConstraint(
            "(state = 'created' AND execution_name IS NULL "
            "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
            "AND bootstrapped_at IS NULL) OR "
            "(state = 'execution_bound' AND execution_name IS NOT NULL "
            "AND bootstrap_request_nonce IS NULL AND bootstrap_capability_digest IS NULL "
            "AND bootstrapped_at IS NULL) OR "
            "(state = 'bootstrapped' AND execution_name IS NOT NULL "
            "AND bootstrap_request_nonce IS NOT NULL AND bootstrap_capability_digest IS NOT NULL "
            "AND bootstrapped_at IS NOT NULL)",
            name="ck_document_transform_qa_run_atomic_state",
        ),
        sa.CheckConstraint(
            "execution_name IS NULL OR (char_length(execution_name) BETWEEN 1 AND 255)",
            name="ck_document_transform_qa_run_execution_name_length",
        ),
        sa.CheckConstraint(
            "bootstrap_capability_digest IS NULL OR octet_length(bootstrap_capability_digest) = 32",
            name="ck_document_transform_qa_run_capability_digest_length",
        ),
    )
    op.create_index(
        "ix_document_transform_qa_runs_created_at",
        "document_transform_qa_runs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("document_transform_qa_runs")
