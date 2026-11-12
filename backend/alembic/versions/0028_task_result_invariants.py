from alembic import op


revision = "0028_task_result_invariants"
down_revision = "0027_google_token_invariants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows are validated atomically. Preserve unexpected records for repair;
    # never coerce a result into another kind or drop task execution evidence.
    op.create_check_constraint(
        "ck_tasks_kind",
        "tasks",
        "kind IN ('file.smart_create', 'file.document_transform')",
    )
    op.create_check_constraint(
        "ck_tasks_result_kind",
        "tasks",
        "status != 'succeeded' OR (("
        "kind = 'file.smart_create' AND result ->> 'task_id' = id::text "
        "AND jsonb_typeof(result -> 'file') = 'object' "
        "AND NOT (result ? 'operation_id') AND NOT (result ? 'output')"
        ") OR ("
        "kind = 'file.document_transform' "
        "AND jsonb_typeof(result -> 'operation_id') = 'string' "
        "AND result ->> 'operation_id' = id::text "
        "AND jsonb_typeof(result -> 'output') = 'object' "
        "AND NOT (result ? 'task_id') AND NOT (result ? 'file')"
        ")) IS TRUE",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_result_kind", "tasks", type_="check")
    op.drop_constraint("ck_tasks_kind", "tasks", type_="check")
