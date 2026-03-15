"""add sort_order to workspace_user_settings

Revision ID: a3e1f8b2c4d6
Revises: f2ceb7c56f6f
Create Date: 2026-03-15 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3e1f8b2c4d6'
down_revision: Union[str, None] = 'f2ceb7c56f6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'workspace_user_settings',
        sa.Column('sort_order', sa.Float(), nullable=False, server_default=sa.text('0')),
    )


def downgrade() -> None:
    op.drop_column('workspace_user_settings', 'sort_order')
