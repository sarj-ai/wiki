"""Re-run the IAM-user grant now that the IAM role exists.

The original grant migration (e5f6a7b8c9d0) deployed in the same merge
that introduced it, so it ran — and stamped — before the Terraform step
that creates the IAM database user. Alembic won't re-run a stamped
revision, so this repeats the identical idempotent grant. No-op
everywhere the membership is already in place.

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IAM_ROLE = "sarj-docs-backend@sarj-docs.iam"
_APP_ROLE = "sarj_wiki_app"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_IAM_ROLE}')
               AND NOT EXISTS (
                   SELECT 1
                   FROM pg_auth_members m
                   JOIN pg_roles granted ON granted.oid = m.roleid
                   JOIN pg_roles member ON member.oid = m.member
                   WHERE granted.rolname = '{_APP_ROLE}'
                     AND member.rolname = '{_IAM_ROLE}'
               ) THEN
                EXECUTE 'GRANT {_APP_ROLE} TO "{_IAM_ROLE}"';
            END IF;
        END
        $$;
        """)


def downgrade() -> None:
    # The membership is owned by e5f6a7b8c9d0's intent; leave it in place.
    pass
