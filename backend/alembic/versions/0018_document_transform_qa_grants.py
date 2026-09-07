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
        sa.Column("source_folder_id", sa.Text(), nullable=False),
        sa.Column("destination_folder_id", sa.Text(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_by_execution", sa.Text()),
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
            "expires_at > created_at",
            name="ck_document_transform_qa_grant_positive_lifetime",
        ),
    )


def downgrade() -> None:
    op.drop_table("document_transform_qa_grants")
