import sqlalchemy as sa

from alembic import op


revision = "0014_mcp_client_grants"
down_revision = "0013_mcp_oauth_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_grants",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("user_session_revision", sa.Integer(), nullable=False),
        sa.Column("transform_allowed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_mcp_grants_user_client", "mcp_grants", ["user_id", "client_id"])
    op.create_index(
        "uq_mcp_grants_active_client",
        "mcp_grants",
        ["user_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("mcp_grants")
