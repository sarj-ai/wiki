from alembic import op


revision = "0027_google_token_invariants"
down_revision = "0026_retire_oauth_proxy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-expiry legacy access tokens are already unusable by the token manager.
    # Keep the refresh credential, session revision and every execution receipt.
    op.execute("""
        UPDATE users SET encrypted_access_token = NULL
        WHERE encrypted_access_token IS NOT NULL AND google_access_token_expires_at IS NULL
    """)
    op.create_check_constraint(
        "ck_users_google_access_pair",
        "users",
        "(encrypted_access_token IS NULL) = (google_access_token_expires_at IS NULL)",
    )
    op.create_check_constraint(
        "ck_users_google_lease_pair",
        "users",
        "(google_refresh_lease_owner IS NULL) = (google_refresh_lease_until IS NULL)",
    )
    op.create_check_constraint(
        "ck_users_google_lease_credential",
        "users",
        "google_refresh_lease_owner IS NULL OR encrypted_refresh_token IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_users_google_generation",
        "users",
        "google_token_generation >= 0",
    )
    op.create_check_constraint("ck_users_token_version", "users", "token_version >= 0")


def downgrade() -> None:
    for name in (
        "ck_users_google_access_pair",
        "ck_users_google_lease_pair",
        "ck_users_google_lease_credential",
        "ck_users_google_generation",
        "ck_users_token_version",
    ):
        op.drop_constraint(name, "users", type_="check")
    # Unusable legacy access ciphertext is not restored. Retained refresh tokens
    # remain the source for new access credentials on either application version.
