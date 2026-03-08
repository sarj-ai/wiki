"""Schema fixes: updated_at trigger and page_permissions check constraint

Revision ID: 002
Revises: 001
Create Date: 2026-03-05

"""
from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create trigger function for auto-updating updated_at
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
        $$ LANGUAGE plpgsql;
    """)

    # Create trigger on pages table
    op.execute("""
        CREATE TRIGGER pages_updated_at BEFORE UPDATE ON pages
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
    """)

    # Add check constraint: at least one of user_id or workspace_id must be set
    op.create_check_constraint(
        "ck_page_permissions_at_least_one_grantee",
        "page_permissions",
        "user_id IS NOT NULL OR workspace_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_page_permissions_at_least_one_grantee", "page_permissions")
    op.execute("DROP TRIGGER IF EXISTS pages_updated_at ON pages")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at()")
