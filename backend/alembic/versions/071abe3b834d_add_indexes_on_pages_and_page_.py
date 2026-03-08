"""Add indexes on pages and page_permissions

Revision ID: 071abe3b834d
Revises: a1c4e7f2b839
Create Date: 2026-03-08 23:52:38.270168

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '071abe3b834d'
down_revision: Union[str, None] = 'a1c4e7f2b839'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_page_permissions_page_id', 'page_permissions', ['page_id'], unique=False)
    op.create_index('ix_page_permissions_user_id', 'page_permissions', ['user_id'], unique=False)
    op.create_index('ix_page_permissions_workspace_id', 'page_permissions', ['workspace_id'], unique=False)
    op.create_index('ix_pages_created_by', 'pages', ['created_by'], unique=False)
    op.create_index('ix_pages_parent_id', 'pages', ['parent_id'], unique=False)
    op.create_index('ix_pages_workspace_id', 'pages', ['workspace_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_pages_workspace_id', table_name='pages')
    op.drop_index('ix_pages_parent_id', table_name='pages')
    op.drop_index('ix_pages_created_by', table_name='pages')
    op.drop_index('ix_page_permissions_workspace_id', table_name='page_permissions')
    op.drop_index('ix_page_permissions_user_id', table_name='page_permissions')
    op.drop_index('ix_page_permissions_page_id', table_name='page_permissions')
