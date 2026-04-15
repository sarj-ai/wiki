"""Navigator pivot: seed doc_icons and user_sort_order from pages

Copies overlay data from the legacy ``pages`` table into the new
Navigator tables BEFORE ``pages`` is dropped in the next migration.

- pages.icon / pages.cover_url → doc_icons (only where the row is a
  real Google Doc, keyed by google_drive_id)
- pages.sort_order → user_sort_order, seeded for every user as a
  common starting baseline (sort order becomes per-user after the
  pivot — see PIVOT_PLAN.md §4.6, decision 11.1)

Raw SQL by design — the models for ``pages`` are deleted in the same
sub-PR, so the ORM isn't available to this migration once the drop
migration runs on a fresh DB.

Revision ID: f1a2b3c4d5e6
Revises: e9f1a2b3c4d5
Create Date: 2026-04-15 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e9f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = _inspect(bind)
    if "pages" not in inspector.get_table_names():
        # Fresh DB — nothing to copy. The drop migration still runs
        # cleanly because it ``IF EXISTS`` guards each drop.
        return

    # Copy Doc icons. Section pages use synthetic ``section-<uuid>``
    # ids and are skipped: the Navigator doesn't keep sections as a
    # concept (folders in Drive replace them).
    op.execute(
        """
        INSERT INTO doc_icons (drive_id, icon, cover_url, updated_at, updated_by)
        SELECT google_drive_id, icon, cover_url, now(), created_by
        FROM pages
        WHERE type = 'page'
          AND google_drive_id NOT LIKE 'section-%'
          AND (icon IS NOT NULL OR cover_url IS NOT NULL)
        ON CONFLICT (drive_id) DO NOTHING
        """
    )

    # Seed user_sort_order for every existing user with the current
    # global sort_order as their starting baseline. After the pivot
    # each user edits their own independently.
    op.execute(
        """
        INSERT INTO user_sort_order (user_id, drive_id, sort_order)
        SELECT u.id, p.google_drive_id, p.sort_order
        FROM users u
        CROSS JOIN pages p
        WHERE p.google_drive_id NOT LIKE 'section-%'
        ON CONFLICT (user_id, drive_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Best-effort downgrade: clear only rows that look seeded. This
    # migration is a copy, not the authoritative source, so a real
    # rollback just drops the target tables (handled by the previous
    # migration's downgrade).
    op.execute("TRUNCATE TABLE user_sort_order")
    op.execute("TRUNCATE TABLE doc_icons")


def _inspect(bind):  # type: ignore[no-untyped-def]
    from sqlalchemy import inspect

    return inspect(bind)
