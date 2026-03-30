"""Add import_jobs table

Revision ID: d7e8f9a0b1c2
Revises: c5d2e3f4a6b8
Create Date: 2026-03-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'd7e8f9a0b1c2'
down_revision: Union[str, None] = 'c5d2e3f4a6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'import_jobs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workspace_id', UUID(as_uuid=True), sa.ForeignKey('workspaces.id'), nullable=True),
        sa.Column('parent_id', UUID(as_uuid=True), sa.ForeignKey('pages.id'), nullable=True),
        sa.Column('format', sa.Text, nullable=False),
        sa.Column('status', sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column('total_items', sa.Integer, nullable=False, server_default=sa.text('0')),
        sa.Column('processed_items', sa.Integer, nullable=False, server_default=sa.text('0')),
        sa.Column('error_message', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_import_jobs_user_id', 'import_jobs', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_import_jobs_user_id', table_name='import_jobs')
    op.drop_table('import_jobs')
