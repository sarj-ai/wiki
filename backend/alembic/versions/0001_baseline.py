from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("email", sa.Text, nullable=False, unique=True),
            sa.Column("google_id", sa.Text, nullable=False, unique=True),
            sa.Column("display_name", sa.Text, nullable=True),
            sa.Column("avatar_url", sa.Text, nullable=True),
            sa.Column("encrypted_access_token", sa.Text, nullable=True),
            sa.Column("encrypted_refresh_token", sa.Text, nullable=True),
            sa.Column(
                "token_version",
                sa.Integer,
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.execute("ALTER TABLE users ALTER COLUMN email TYPE citext USING email::citext")
        op.create_check_constraint(
            "ck_users_email_domain",
            "users",
            "email LIKE '%@sarj.ai'",
        )

    if "drive_tree_cache" not in tables:
        op.create_table(
            "drive_tree_cache",
            sa.Column("drive_id", sa.Text, primary_key=True),
            sa.Column("tree", sa.Text, nullable=False),
            sa.Column(
                "refreshed_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "refreshed_by",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_drive_tree_cache_refreshed_at",
            "drive_tree_cache",
            ["refreshed_at"],
        )
        op.create_index(
            "ix_drive_tree_cache_refreshed_by",
            "drive_tree_cache",
            ["refreshed_by"],
            postgresql_where=sa.text("refreshed_by IS NOT NULL"),
        )

    if "doc_icons" not in tables:
        op.create_table(
            "doc_icons",
            sa.Column("drive_id", sa.Text, primary_key=True),
            sa.Column("icon", sa.Text, nullable=True),
            sa.Column("cover_url", sa.Text, nullable=True),
            sa.Column(
                "updated_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_by",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_doc_icons_updated_by",
            "doc_icons",
            ["updated_by"],
            postgresql_where=sa.text("updated_by IS NOT NULL"),
        )

    if "user_folder_icons" not in tables:
        op.create_table(
            "user_folder_icons",
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("folder_drive_id", sa.Text, nullable=False),
            sa.Column("icon", sa.Text, nullable=True),
            sa.Column(
                "updated_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("user_id", "folder_drive_id"),
        )

    if "user_sort_order" not in tables:
        op.create_table(
            "user_sort_order",
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("drive_id", sa.Text, nullable=False),
            sa.Column("sort_order", sa.Double, nullable=False),
            sa.PrimaryKeyConstraint("user_id", "drive_id"),
        )
        op.create_check_constraint(
            "ck_user_sort_order_finite",
            "user_sort_order",
            "sort_order > '-Infinity'::float8 AND sort_order < 'Infinity'::float8",
        )

    if "user_preferences" not in tables:
        op.create_table(
            "user_preferences",
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "show_non_doc_files",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "hidden_workspace_ids",
                ARRAY(sa.Text),
                nullable=False,
                server_default=sa.text("'{}'::text[]"),
            ),
            sa.Column(
                "updated_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sarj-docs-backend@sarj-docs.iam')
               AND NOT EXISTS (
                   SELECT 1
                   FROM pg_auth_members m
                   JOIN pg_roles granted ON granted.oid = m.roleid
                   JOIN pg_roles member ON member.oid = m.member
                   WHERE granted.rolname = 'sarj_wiki_app'
                     AND member.rolname = 'sarj-docs-backend@sarj-docs.iam'
               ) THEN
                EXECUTE 'GRANT sarj_wiki_app TO "sarj-docs-backend@sarj-docs.iam"';
            END IF;
        END
        $$;
        """)


def downgrade() -> None:
    msg = "baseline migration; restore from a DB snapshot"
    raise NotImplementedError(msg)
