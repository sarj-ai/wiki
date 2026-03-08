"""add cached_content to pages

Revision ID: a1c4e7f2b839
Revises: 9bbdf6a300f6
Create Date: 2026-03-08 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'a1c4e7f2b839'
down_revision: Union[str, None] = '9bbdf6a300f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pages', sa.Column('cached_content', JSONB(), nullable=True))
    op.add_column('pages', sa.Column('content_cached_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('pages', 'content_cached_at')
    op.drop_column('pages', 'cached_content')
