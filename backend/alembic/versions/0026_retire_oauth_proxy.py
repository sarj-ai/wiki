from alembic import op


revision = "0026_retire_oauth_proxy"
down_revision = "0025_simplify_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use the runtime's stable registration lock and pause cache cleanup during transfer.
    op.execute("SELECT pg_advisory_xact_lock(6289641132649566769)")
    op.execute("LOCK TABLE mcp_registration_admission, cache_entries IN ACCESS EXCLUSIVE MODE")
    op.create_check_constraint(
        "ck_cache_registration_history",
        "cache_entries",
        """namespace <> 'mcp.registration' OR
        (key = 'global' AND CASE WHEN jsonb_typeof(value) = 'array' THEN
          jsonb_array_length(value) <= 300 AND
          NOT jsonb_path_exists(value, '$[*] ? (@.type() != "string")')
        ELSE false END)""",
    )
    op.execute("""
        INSERT INTO cache_entries (namespace, key, value, expires_at)
        SELECT 'mcp.registration', 'global', to_jsonb(admitted_at),
               COALESCE((SELECT max(stamp) FROM unnest(admitted_at) AS stamp), now())
                   + interval '30 minutes'
        FROM mcp_registration_admission
    """)
    op.drop_table("mcp_registration_admission")
    # Provider read caches are disposable; registration history is durable for its window.
    op.execute("DELETE FROM cache_entries WHERE namespace <> 'mcp.registration'")
    op.drop_index("ix_cache_entries_legacy_namespace", table_name="cache_entries")
    # The active native issuer has replaced every proxy collection.
    op.execute("DELETE FROM mcp_oauth_entries WHERE collection NOT LIKE 'native:%'")
    op.drop_column("mcp_oauth_entries", "updated_at")
    for column in ("created_by", "created_at", "retired_by"):
        op.drop_column("document_aliases", column)


def downgrade() -> None:
    message = "Retired OAuth proxy state requires a database backup to restore"
    raise NotImplementedError(message)
