import sqlalchemy as sa

from alembic import op


revision = "0017_terminal_transform_replay"
down_revision = "0016_mcp_registration_admission"
branch_labels = None
depends_on = None
_INDEX_NAME = "ix_tasks_doc_transform_terminal_semantic"


def upgrade() -> None:
    # This migration may run while the previous revision is still admitting
    # transforms. Build without blocking task writes. Dropping first makes a
    # retry safe if PostgreSQL retained an INVALID index after interruption.
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
        op.create_index(
            _INDEX_NAME,
            "tasks",
            [
                "owner_user_id",
                "kind",
                "status",
                "request_hash",
                sa.text("finished_at DESC"),
                sa.text("id DESC"),
            ],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX_NAME,
            table_name="tasks",
            postgresql_concurrently=True,
        )
