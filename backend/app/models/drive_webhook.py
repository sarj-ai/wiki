from datetime import datetime

from sqlalchemy import DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DriveWebhookChannel(Base):
    __tablename__ = "drive_webhook_channels"

    channel_id: Mapped[str] = mapped_column(Text, primary_key=True)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    folder_id: Mapped[str] = mapped_column(Text, nullable=False)
    page_token: Mapped[str | None] = mapped_column(Text)  # Changes API cursor
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
