import sqlalchemy as sa

from alembic import op


revision = "0023_storage_hot_paths"
down_revision = "0022_qa_corpus_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_cache_entries_legacy_namespace",
        "cache_entries",
        ["namespace"],
        postgresql_where=sa.text(r"key !~ '^h[0-9]+\.'"),
    )
    op.create_index("ix_tasks_expires_at", "tasks", ["expires_at"])
    op.create_index(
        "ix_tasks_pending_document_dispatch",
        "tasks",
        ["dispatch_ready_at", "id"],
        postgresql_where=sa.text("kind = 'file.document_transform' AND status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_pending_document_dispatch", table_name="tasks")
    op.drop_index("ix_tasks_expires_at", table_name="tasks")
    op.drop_index("ix_cache_entries_legacy_namespace", table_name="cache_entries")
