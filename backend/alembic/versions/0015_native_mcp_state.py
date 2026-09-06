import sqlalchemy as sa

from alembic import op


revision = "0015_native_mcp_state"
down_revision = "0014_mcp_client_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_native_state",
        sa.Column("purpose", sa.Text(), primary_key=True),
        sa.Column("key_digest", sa.Text(), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_mcp_native_state_expires_at", "mcp_native_state", ["expires_at"])


def downgrade() -> None:
    op.drop_table("mcp_native_state")
