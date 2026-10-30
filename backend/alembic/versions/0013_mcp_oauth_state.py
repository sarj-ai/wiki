# Dedicated encrypted MCP OAuth state; feature activation is independent.
import sqlalchemy as sa

from alembic import op


revision = "0013_mcp_oauth_state"
down_revision = "0012_header_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_entries",
        sa.Column("collection", sa.Text(), primary_key=True),
        sa.Column("key_digest", sa.Text(), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_mcp_oauth_entries_expires_at", "mcp_oauth_entries", ["expires_at"])


def downgrade() -> None:
    op.drop_table("mcp_oauth_entries")
