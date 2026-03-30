"""Add inheritable flag to page_permissions

Revision ID: b4f1a2c3d5e7
Revises: 6200c8446acc
Create Date: 2026-03-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4f1a2c3d5e7'
down_revision: Union[str, None] = '6200c8446acc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'page_permissions',
        sa.Column('inheritable', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('page_permissions', 'inheritable')
