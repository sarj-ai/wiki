"""add search_vector to pages

Revision ID: 54bfb520e4fe
Revises: 071abe3b834d
Create Date: 2026-03-10 18:31:16.498592

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR


# revision identifiers, used by Alembic.
revision: str = '54bfb520e4fe'
down_revision: Union[str, None] = '071abe3b834d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add tsvector column
    op.add_column('pages', sa.Column('search_vector', TSVECTOR))

    # Create GIN index for fast full-text search
    op.create_index(
        'ix_pages_search_vector',
        'pages',
        ['search_vector'],
        postgresql_using='gin',
    )

    # Backfill existing rows: populate search_vector from title
    op.execute("""
        UPDATE pages
        SET search_vector = to_tsvector('english', coalesce(title, ''))
    """)


def downgrade() -> None:
    op.drop_index('ix_pages_search_vector', table_name='pages')
    op.drop_column('pages', 'search_vector')
