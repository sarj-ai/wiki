from datetime import datetime
from typing import NewType
import uuid

from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import UserId
from app.mcp.identity import McpClientId


McpGrantId = NewType("McpGrantId", uuid.UUID)


class McpGrant(Base):
    __tablename__ = "mcp_grants"
    __table_args__ = (
        Index("ix_mcp_grants_user_client", "user_id", "client_id"),
        Index(
            "uq_mcp_grants_active_client",
            "user_id",
            "client_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[McpGrantId] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UserId] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[McpClientId] = mapped_column(Text)
    user_session_revision: Mapped[int]
    transform_allowed: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
