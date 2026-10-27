from alembic import op


revision = "0024_configurable_email_domain"
down_revision = "0023_storage_hot_paths"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_users_email_domain", "users", type_="check", if_exists=True)


def downgrade() -> None:
    op.create_check_constraint("ck_users_email_domain", "users", "email LIKE '%@sarj.ai'")
