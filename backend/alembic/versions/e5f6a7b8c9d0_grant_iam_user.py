"""Grant the backend's IAM database user membership in sarj_wiki_app.

Terraform creates the IAM principal (LOGIN only); object privileges
can't be granted from Terraform, so this migration runs the GRANT.
Role membership — rather than per-object grants — makes the IAM user
inherit ownership rights too, so future startup-time DDL migrations
keep working after the password login is removed.

Idempotent and environment-tolerant: a no-op when the IAM role doesn't
exist (local dev, CI) or the membership is already in place.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
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
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_IAM_ROLE}') THEN
                EXECUTE 'REVOKE {_APP_ROLE} FROM "{_IAM_ROLE}"';
            END IF;
        END
        $$;
        """)
