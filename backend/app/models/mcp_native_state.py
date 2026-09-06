from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class McpNativeState(Base):
    __tablename__ = "mcp_native_state"

    purpose: Mapped[str] = mapped_column(Text, primary_key=True)
    key_digest: Mapped[str] = mapped_column(Text, primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
