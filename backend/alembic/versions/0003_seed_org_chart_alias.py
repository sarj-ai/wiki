from typing import TYPE_CHECKING

import sqlalchemy as sa

from alembic import op


if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0003_seed_org_chart_alias"
down_revision: str | None = "0002_document_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
        DO $$
        DECLARE
            alias_name CONSTANT text := 'org-chart';
            target_file_id CONSTANT text := '19gC3bmeBOI43-fw-ZfPJE4xNVlHIVUn7';
            claimed_file_id text;
        BEGIN
            SELECT file_id
              INTO claimed_file_id
              FROM document_aliases
             WHERE alias = alias_name
               FOR UPDATE;

            IF claimed_file_id IS NOT NULL
               AND claimed_file_id <> target_file_id THEN
                RAISE EXCEPTION
                    'org-chart is already reserved for a different document';
            END IF;

            IF EXISTS (
                SELECT 1
                  FROM document_aliases
                 WHERE file_id = target_file_id
                   AND retired_at IS NULL
                   AND alias <> alias_name
            ) THEN
                RAISE EXCEPTION
                    'organization chart already has a different active alias';
            END IF;

            INSERT INTO document_aliases (alias, file_id)
            VALUES (alias_name, target_file_id)
            ON CONFLICT (alias) DO UPDATE
                SET retired_at = NULL,
                    retired_by = NULL
              WHERE document_aliases.file_id = EXCLUDED.file_id;
        END
        $$;
        """,
        ),
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
        DELETE FROM document_aliases
         WHERE alias = 'org-chart'
           AND file_id = '19gC3bmeBOI43-fw-ZfPJE4xNVlHIVUn7'
           AND created_by IS NULL;
        """,
        ),
    )
