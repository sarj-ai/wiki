from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MCPOAuthEntry(Base):
    __tablename__ = "mcp_oauth_entries"

    collection: Mapped[str] = mapped_column(Text, primary_key=True)
    key_digest: Mapped[str] = mapped_column(Text, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
