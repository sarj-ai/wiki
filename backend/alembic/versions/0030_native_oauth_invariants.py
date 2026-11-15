import sqlalchemy as sa

from alembic import op


revision = "0030_native_oauth_invariants"
down_revision = "0029_task_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "mcp_oauth_entries", "expires_at", existing_type=sa.DateTime(timezone=True), nullable=False
    )
    op.create_check_constraint(
        "ck_mcp_oauth_native_collection",
        "mcp_oauth_entries",
        "collection IN ('native:client', 'native:intent', 'native:code', 'native:access', 'native:refresh')",
    )
    op.create_check_constraint(
        "ck_mcp_oauth_key_digest", "mcp_oauth_entries", "key_digest ~ '^[0-9a-f]{64}$'"
    )


def downgrade() -> None:
    op.drop_constraint("ck_mcp_oauth_key_digest", "mcp_oauth_entries", type_="check")
    op.drop_constraint("ck_mcp_oauth_native_collection", "mcp_oauth_entries", type_="check")
    op.alter_column(
        "mcp_oauth_entries", "expires_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )
