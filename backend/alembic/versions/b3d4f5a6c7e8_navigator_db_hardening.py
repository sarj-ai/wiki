"""Navigator DB hardening: citext email + integrity CHECKs.

Three coupled changes that all live in one migration so a half-applied
state never appears:

1. ``users.email`` becomes ``citext`` (Stk-S10) so ``foo@Sarj.ai`` and
   ``foo@sarj.ai`` collide on the unique index and can't fork into
   parallel User rows when Google returns either casing.

2. ``CHECK (email LIKE '%@sarj.ai')`` (Stk-S11) backstops the
   application-level domain enforcement in ``AuthService._enforce_domain``.
   The pattern anchors on the literal ``@sarj.ai`` suffix so cousins
   like ``evil-sarj.ai`` don't slip through. ``citext`` makes LIKE
   case-insensitive, so ``USER@SARJ.AI`` matches.

3. ``CHECK`` on ``user_sort_order.sort_order`` (Stk-S12) rejects NaN,
   +Infinity, and -Infinity. Postgres treats NaN as greater than every
   non-NaN, so ``sort_order < 'Infinity'`` simultaneously rejects NaN
   and +Infinity; the ``> '-Infinity'`` half handles the negative
   sentinel. A NaN/Inf in this column would let drag-drop produce
   "ordering" that the comparator can never resolve.

Revision ID: b3d4f5a6c7e8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-28 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b3d4f5a6c7e8"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    # Lowercase any pre-existing rows so the new check + citext index
    # don't trip on legacy mixed-case data. citext stores the original
    # casing, so this also normalizes display.
    op.execute("UPDATE users SET email = lower(email) WHERE email <> lower(email)")
    op.execute("ALTER TABLE users ALTER COLUMN email TYPE citext USING email::citext")
    op.create_check_constraint(
        "ck_users_email_domain",
        "users",
        "email LIKE '%@sarj.ai'",
    )
    op.create_check_constraint(
        "ck_user_sort_order_finite",
        "user_sort_order",
        "sort_order > '-Infinity'::float8 AND sort_order < 'Infinity'::float8",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_sort_order_finite", "user_sort_order", type_="check")
    op.drop_constraint("ck_users_email_domain", "users", type_="check")
    op.execute("ALTER TABLE users ALTER COLUMN email TYPE text USING email::text")
    # Leave the citext extension installed — dropping it would fail if
    # any other column adopted it, and the extension itself is harmless.
