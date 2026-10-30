from __future__ import annotations

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import IdempotencyKey, TaskId, UserId
from app.tasks.schemas import TaskKind


class TaskReplayKey(Base):
    __tablename__ = "task_replay_keys"

    owner_user_id: Mapped[UserId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[TaskKind] = mapped_column(Text, primary_key=True)
    idempotency_key: Mapped[IdempotencyKey] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[TaskId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
