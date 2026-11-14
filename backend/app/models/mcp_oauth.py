from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MCPOAuthEntry(Base):
    __tablename__ = "mcp_oauth_entries"
    __table_args__ = (
        CheckConstraint(
            "collection IN ('native:client', 'native:intent', 'native:code', "
            "'native:access', 'native:refresh')",
            name="ck_mcp_oauth_native_collection",
        ),
        CheckConstraint("key_digest ~ '^[0-9a-f]{64}$'", name="ck_mcp_oauth_key_digest"),
    )

    collection: Mapped[str] = mapped_column(Text, primary_key=True)
    key_digest: Mapped[str] = mapped_column(Text, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
