from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0003_seed_org_chart_alias"
down_revision: str | None = "0002_document_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Preserve the applied revision without distributing deployment-owned seed data.
def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
