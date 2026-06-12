from datetime import datetime
import uuid

from sqlalchemy import DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # Stored as Postgres ``citext`` (see migration ``b3d4f5a6c7e8``) so
    # ``foo@Sarj.ai`` and ``foo@sarj.ai`` collide on the unique index.
    # SQLAlchemy treats it as ``Text`` end-to-end; the case-insensitive
    # comparison happens in Postgres.
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    google_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    # Incremented on logout to invalidate all existing JWTs
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
