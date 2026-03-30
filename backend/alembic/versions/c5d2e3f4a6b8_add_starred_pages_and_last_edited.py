"""Add starred_pages table and last_edited_by column

Revision ID: c5d2e3f4a6b8
Revises: b4f1a2c3d5e7
Create Date: 2026-03-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c5d2e3f4a6b8'
down_revision: Union[str, None] = 'b4f1a2c3d5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create starred_pages table
    op.create_table(
        'starred_pages',
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('page_id', UUID(as_uuid=True), sa.ForeignKey('pages.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('starred_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_starred_pages_user_id', 'starred_pages', ['user_id'])

    # Add last_edited_by column to pages
    op.add_column(
        'pages',
        sa.Column('last_edited_by', UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('pages', 'last_edited_by')
    op.drop_index('ix_starred_pages_user_id', table_name='starred_pages')
    op.drop_table('starred_pages')
