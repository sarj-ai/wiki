"""Add token_version and permission constraint

Revision ID: 6200c8446acc
Revises: a3e1f8b2c4d6
Create Date: 2026-03-19 00:19:56.908104

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6200c8446acc'
down_revision: Union[str, None] = 'a3e1f8b2c4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('token_version', sa.Integer(), server_default=sa.text('0'), nullable=False))
    # CHECK constraint may already exist from model definition — skip if so
    from sqlalchemy import inspect as sa_inspect
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing = [c['name'] for c in inspector.get_check_constraints('page_permissions')]
    if 'ck_page_permissions_at_least_one_grantee' not in existing:
        op.create_check_constraint(
            'ck_page_permissions_at_least_one_grantee',
            'page_permissions',
            'user_id IS NOT NULL OR workspace_id IS NOT NULL',
        )


def downgrade() -> None:
    op.drop_constraint('ck_page_permissions_at_least_one_grantee', 'page_permissions', type_='check')
    op.drop_column('users', 'token_version')
