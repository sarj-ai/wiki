from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import FileId, IdempotencyKey, TaskId
from app.tasks.schemas import TaskKind, TaskStatus


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_tasks_status",
        ),
        CheckConstraint("char_length(request_hash) = 64", name="ck_tasks_request_hash"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_tasks_payload_object"),
        CheckConstraint(
            "result IS NULL OR jsonb_typeof(result) = 'object'",
            name="ck_tasks_result_object",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_tasks_attempt_count_nonnegative"),
        CheckConstraint(
            "checkpoint IS NULL OR jsonb_typeof(checkpoint) = 'object'",
            name="ck_tasks_checkpoint_object",
        ),
        CheckConstraint(
            "checkpoint_generation >= 0",
            name="ck_tasks_checkpoint_generation_nonnegative",
        ),
        CheckConstraint(
            "(status = 'pending' AND attempt_count = 0) OR "
            "(status = 'cancelled' AND attempt_count = 0) OR "
            "status IN ('running', 'succeeded', 'failed') AND attempt_count > 0",
            name="ck_tasks_attempt_count_matches_state",
        ),
        CheckConstraint("expires_at > created_at", name="ck_tasks_expiry_after_creation"),
        CheckConstraint(
            "lease_expires_at IS NULL OR lease_expires_at > started_at",
            name="ck_tasks_lease_after_start",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="ck_tasks_finish_after_start",
        ),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND finished_at IS NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND finished_at IS NULL "
            "AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL AND result IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NOT NULL "
            "AND error_code IS NULL AND error_detail IS NULL) OR "
            "(status = 'failed' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
            "AND error_code IS NOT NULL) OR "
            "(status = 'cancelled' AND started_at IS NULL AND finished_at IS NOT NULL "
            "AND lease_token IS NULL AND lease_expires_at IS NULL AND result IS NULL "
            "AND error_code IS NULL AND error_detail IS NULL)",
            name="ck_tasks_legal_state",
        ),
        UniqueConstraint(
            "owner_user_id",
            "kind",
            "idempotency_key",
            name="uq_tasks_owner_kind_idempotency",
        ),
        Index("ix_tasks_owner_created", "owner_user_id", "created_at"),
        Index("ix_tasks_status_lease", "status", "lease_expires_at"),
        Index("ix_tasks_expires_at", "expires_at"),
        Index(
            "ix_tasks_pending_document_dispatch",
            "dispatch_ready_at",
            "id",
            postgresql_where=text("kind = 'file.document_transform' AND status = 'pending'"),
        ),
        Index(
            "ix_tasks_doc_transform_terminal_semantic",
            "owner_user_id",
            "kind",
            "status",
            "request_hash",
            text("finished_at DESC"),
            text("id DESC"),
        ),
        Index(
            "uq_tasks_active_in_place_target",
            "in_place_target",
            unique=True,
            postgresql_where=text(
                "in_place_target IS NOT NULL AND status IN ('pending', 'running')"
            ),
        ),
    )

    id: Mapped[TaskId] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[TaskKind] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
    )
    idempotency_key: Mapped[IdempotencyKey] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    checkpoint: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    checkpoint_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    resource_id: Mapped[FileId | None] = mapped_column(Text, nullable=True)
    in_place_target: Mapped[FileId | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
