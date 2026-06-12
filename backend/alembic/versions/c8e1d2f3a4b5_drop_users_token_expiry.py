"""Drop unused users.token_expiry.

The column was written on every login but never read: credentials are
built without an expiry, so google-auth refreshes on demand via the
refresh token. Dropping it removes a token-lifetime breadcrumb from the
database for free.

Revision ID: c8e1d2f3a4b5
Revises: b3d4f5a6c7e8
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "c8e1d2f3a4b5"
down_revision: str | None = "b3d4f5a6c7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("users", "token_expiry")


def downgrade() -> None:
    # Restores the column shape only; the dropped values are gone.
    op.add_column("users", sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True))
