from datetime import datetime
import uuid

from sqlalchemy import DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import GoogleUserId, UserId


class User(Base):
    __tablename__ = "users"

    id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    google_id: Mapped[GoogleUserId] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    google_access_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    google_token_generation: Mapped[int] = mapped_column(
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    google_refresh_lease_owner: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    google_refresh_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_version: Mapped[int] = mapped_column(
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
