from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import AuthSessionFamilyId, AuthSessionId, TokenId, UserId


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_family_id", "family_id"),
        Index("ix_user_sessions_user_id", "user_id"),
    )

    id: Mapped[AuthSessionId] = mapped_column(UUID(as_uuid=True), primary_key=True)
    family_id: Mapped[AuthSessionFamilyId] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_jti: Mapped[TokenId] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
