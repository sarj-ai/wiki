import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op


revision = "0025_simplify_storage"
down_revision = "0024_configurable_email_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve concurrent settings edits and one-time OAuth consumption during transfer.
    op.execute(
        "LOCK TABLE users, user_preferences, user_folder_icons, user_sort_order, mcp_native_state "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.add_column(
        "users",
        sa.Column("show_non_doc_files", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "users",
        sa.Column(
            "hidden_workspace_ids",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("folder_icons", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "users",
        sa.Column("sort_orders", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.execute("""
        UPDATE users SET show_non_doc_files = prefs.show_non_doc_files,
                         hidden_workspace_ids = prefs.hidden_workspace_ids
        FROM user_preferences AS prefs WHERE prefs.user_id = users.id
    """)
    op.execute(
        "UPDATE users SET folder_icons = COALESCE("  # sarj-noqa: SARJ020 -- One-time transactional migration must preserve every legacy icon in its owner's JSON map.
        "(SELECT jsonb_object_agg(folder_drive_id, icon) FROM user_folder_icons "
        "WHERE user_id = users.id), '{}'::jsonb)"
    )
    op.execute(
        "UPDATE users SET sort_orders = COALESCE("  # sarj-noqa: SARJ020 -- One-time transactional migration must preserve every legacy sort value in its owner's JSON map.
        "(SELECT jsonb_object_agg(drive_id, sort_order) FROM user_sort_order "
        "WHERE user_id = users.id), '{}'::jsonb)"
    )
    for table in ("user_folder_icons", "user_sort_order", "user_preferences"):
        op.drop_table(table)

    op.drop_table("drive_tree_cache", if_exists=True)
    op.drop_column("user_drive_tree_cache", "refreshed_by")
    for column in ("cover_url", "updated_at", "updated_by"):
        op.drop_column("doc_icons", column)

    # Preserve the encryption namespace, digest and ciphertext byte-for-byte.
    # An unexpected collision aborts the migration instead of replacing a token.
    op.execute("""
        INSERT INTO mcp_oauth_entries (collection, key_digest, ciphertext, expires_at)
        SELECT 'native:' || purpose, key_digest, ciphertext, expires_at FROM mcp_native_state
    """)
    op.drop_table("mcp_native_state")

    for table in (
        "document_transform_qa_corpus_submissions",
        "document_transform_qa_corpus_members",
        "document_transform_qa_corpus_campaigns",
        "document_transform_qa_runs",
        "document_transform_qa_grants",
    ):
        op.drop_table(table)
    for function in (
        "recheck_qa_corpus_campaign()",
        "validate_qa_corpus_campaign(uuid)",
        "guard_qa_corpus_submission_mutation()",
        "guard_qa_corpus_member_mutation()",
        "guard_qa_corpus_campaign_update()",
    ):
        op.execute(f"DROP FUNCTION {function}")
    # These internal jobs cannot be resumed after their issuer and workload retire.
    # Their Drive outputs remain untouched; replay keys cascade with the task.
    op.execute(
        "DELETE FROM tasks WHERE payload ? 'qa_contract' OR result ? 'qa_permission_evidence'"
    )


def downgrade() -> None:
    message = "Cloud QA retirement is irreversible; restore a database backup to roll back"
    raise NotImplementedError(message)
