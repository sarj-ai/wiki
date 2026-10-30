import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision = "0016_mcp_registration_admission"
down_revision = "0015_native_mcp_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_registration_admission",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admitted_at", postgresql.ARRAY(sa.DateTime(timezone=True)), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_mcp_registration_singleton"),
        sa.CheckConstraint(
            "cardinality(admitted_at) <= 300 AND "
            "(cardinality(admitted_at) = 0 OR array_ndims(admitted_at) = 1) AND "
            "array_position(admitted_at, NULL) IS NULL",
            name="ck_mcp_registration_bounded_history",
        ),
    )


def downgrade() -> None:
    op.drop_table("mcp_registration_admission")
