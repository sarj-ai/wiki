import sqlalchemy as sa

from alembic import op


revision = "0018_transform_qa_grants"
down_revision = "0017_terminal_transform_replay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_transform_qa_grants",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("token_digest", sa.LargeBinary(length=32), nullable=False, unique=True),
        sa.Column(
            "issuer_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_by_execution", sa.Text()),
        sa.Column("capability_digest", sa.LargeBinary(length=32), unique=True),
        sa.Column("previous_capability_digest", sa.LargeBinary(length=32)),
        sa.Column("consume_request_nonce", sa.UUID()),
        sa.Column("renewal_request_nonce", sa.UUID()),
        sa.Column("capability_expires_at", sa.DateTime(timezone=True)),
        sa.Column("job_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_renewed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "release_sha ~ '^[0-9a-f]{40}$'",
            name="ck_document_transform_qa_grant_release_sha",
        ),
        sa.CheckConstraint(
            "mode IN ('format', 'unslop', 'both')",
            name="ck_document_transform_qa_grant_mode",
        ),
        sa.CheckConstraint(
            "source_folder_id <> destination_folder_id",
            name="ck_document_transform_qa_grant_distinct_folders",
        ),
        sa.CheckConstraint(
            "expires_at > created_at AND expires_at <= created_at + interval '15 minutes'",
            name="ck_document_transform_qa_grant_bounded_lifetime",
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL AND consumed_by_execution IS NULL AND capability_digest IS NULL "
            "AND previous_capability_digest IS NULL AND consume_request_nonce IS NULL "
            "AND renewal_request_nonce IS NULL AND capability_expires_at IS NULL "
            "AND job_expires_at IS NULL) OR "
            "(consumed_at IS NOT NULL AND consumed_by_execution IS NOT NULL "
            "AND capability_digest IS NOT NULL AND consume_request_nonce IS NOT NULL "
            "AND capability_expires_at IS NOT NULL "
            "AND job_expires_at IS NOT NULL AND capability_expires_at <= job_expires_at)",
            name="ck_document_transform_qa_grant_atomic_state",
        ),
        sa.CheckConstraint(
            "(previous_capability_digest IS NULL AND renewal_request_nonce IS NULL) OR "
            "(previous_capability_digest IS NOT NULL AND renewal_request_nonce IS NOT NULL)",
            name="ck_document_transform_qa_grant_renewal_replay_state",
        ),
    )
    op.create_index(
        "ix_document_transform_qa_grants_expires_at",
        "document_transform_qa_grants",
        ["expires_at"],
    )
    op.create_index(
        "ix_document_transform_qa_grants_job_expires_at",
        "document_transform_qa_grants",
        ["job_expires_at"],
    )


def downgrade() -> None:
    op.drop_table("document_transform_qa_grants")
