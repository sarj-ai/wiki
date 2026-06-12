"""Encrypt drive_tree_cache.tree at rest.

The column changes from plaintext JSONB to Fernet ciphertext (text).
Existing rows are dropped rather than re-encrypted — the table is a
pure cache rebuilt from Drive on the next sidebar load.

Revision ID: d4e5f6a7b8c9
Revises: c8e1d2f3a4b5
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c8e1d2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM drive_tree_cache")
    op.execute("ALTER TABLE drive_tree_cache ALTER COLUMN tree TYPE text USING tree::text")


def downgrade() -> None:
    # Ciphertext can't be cast back to JSONB — drop and let the cache rebuild.
    op.execute("DELETE FROM drive_tree_cache")
    op.execute("ALTER TABLE drive_tree_cache ALTER COLUMN tree TYPE jsonb USING tree::jsonb")
