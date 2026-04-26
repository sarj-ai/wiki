"""FK supporting indexes on *_by audit columns.

Adds partial indexes on ``drive_tree_cache.refreshed_by`` and
``doc_icons.updated_by`` so deleting a user (which fires
``ON DELETE SET NULL``) doesn't sequentially scan the whole table.
``WHERE col IS NOT NULL`` keeps the index small — most rows have a
non-null actor, but the partial form means the index doesn't bloat
with NULL entries from rows whose acting user was already deleted.

Revision ID: a1b2c3d4e5f6
Revises: f2b3c4d5e6f7
Create Date: 2026-04-26 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_drive_tree_cache_refreshed_by",
        "drive_tree_cache",
        ["refreshed_by"],
        postgresql_where=sa.text("refreshed_by IS NOT NULL"),
    )
    op.create_index(
        "ix_doc_icons_updated_by",
        "doc_icons",
        ["updated_by"],
        postgresql_where=sa.text("updated_by IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_doc_icons_updated_by", table_name="doc_icons")
    op.drop_index("ix_drive_tree_cache_refreshed_by", table_name="drive_tree_cache")
