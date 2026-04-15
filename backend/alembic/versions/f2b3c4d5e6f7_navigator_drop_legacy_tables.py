"""Navigator pivot: drop all legacy Sarj Docs tables

Removes every table the Navigator no longer uses (see PIVOT_PLAN.md
§6.2). Runs after the data migration that seeds ``doc_icons`` and
``user_sort_order`` from ``pages``.

Tables dropped:
- starred_pages, page_permissions (FK-dependent on pages, dropped first)
- pages (the legacy tree)
- workspace_memberships, workspace_user_settings, workspace_group_access
  (FK-dependent on workspaces, dropped first)
- workspaces
- drive_webhook_channels (no more Drive push subscriptions)
- import_jobs (no more Notion importer)
- Associated GIN / btree indexes come along with the table drops

``IF EXISTS`` on every drop so the migration is idempotent against a
fresh DB that never had the old tables.

Revision ID: f2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-15 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2b3c4d5e6f7"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop FK-dependent children before the tables they reference.
    op.execute("DROP TABLE IF EXISTS starred_pages CASCADE")
    op.execute("DROP TABLE IF EXISTS page_permissions CASCADE")
    op.execute("DROP TABLE IF EXISTS pages CASCADE")

    op.execute("DROP TABLE IF EXISTS workspace_memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS workspace_user_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS workspace_group_access CASCADE")
    op.execute("DROP TABLE IF EXISTS workspaces CASCADE")

    op.execute("DROP TABLE IF EXISTS drive_webhook_channels CASCADE")
    op.execute("DROP TABLE IF EXISTS import_jobs CASCADE")


def downgrade() -> None:
    # No downgrade path. These tables held the old Sarj Docs data
    # model and cannot be rebuilt without the full legacy schema —
    # recover from a DB snapshot if this migration needs to be
    # reversed in prod.
    raise NotImplementedError(
        "Navigator drop-legacy-tables migration is not reversible. "
        "Restore from a pre-cutover DB snapshot to recover."
    )
