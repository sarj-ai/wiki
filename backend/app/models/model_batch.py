from __future__ import annotations

from datetime import datetime

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.identifiers import (
    ModelBatchJobId,
    ModelCallId,
    ModelId,
    ModelRequestHash,
    TaskId,
    VertexBatchJobName,
)


class ModelBatchJobRow(Base):
    __tablename__ = "model_batch_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('prepared', 'submitting', 'running', 'ingested', 'failed')",
            name="ck_model_batch_jobs_status",
        ),
        CheckConstraint(
            "(status IN ('prepared', 'submitting') AND vertex_job_name IS NULL "
            "AND provider_state IS NULL) OR "
            "(status = 'running' AND vertex_job_name IS NOT NULL AND provider_state IS NULL) OR "
            "(status = 'ingested' AND vertex_job_name IS NOT NULL "
            "AND provider_state IN ('succeeded', 'partially_succeeded')) OR "
            "(status = 'failed' AND provider_state IN ('failed', 'cancelled', 'expired', "
            "'rejected') AND (provider_state = 'rejected') = (vertex_job_name IS NULL))",
            name="ck_model_batch_jobs_legal_state",
        ),
        Index("ix_model_batch_jobs_task", "task_id"),
        Index(
            "ix_model_batch_jobs_active",
            "updated_at",
            "id",
            postgresql_where=text("status IN ('prepared', 'submitting', 'running')"),
        ),
    )

    id: Mapped[ModelBatchJobId] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[TaskId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[ModelId] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    vertex_job_name: Mapped[VertexBatchJobName | None] = mapped_column(Text, nullable=True)
    provider_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ModelCallRow(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'queued', 'succeeded', 'failed')",
            name="ck_model_calls_status",
        ),
        CheckConstraint("char_length(request_sha256) = 64", name="ck_model_calls_request_hash"),
        CheckConstraint("occurrence > 0", name="ck_model_calls_occurrence_positive"),
        CheckConstraint(
            "(status = 'pending' AND job_id IS NULL AND outcome_ciphertext IS NULL) OR "
            "(status = 'queued' AND job_id IS NOT NULL AND outcome_ciphertext IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND job_id IS NOT NULL "
            "AND outcome_ciphertext IS NOT NULL)",
            name="ck_model_calls_legal_state",
        ),
        UniqueConstraint(
            "task_id", "request_sha256", "occurrence", name="uq_model_calls_task_request"
        ),
        Index("ix_model_calls_job", "job_id"),
    )

    id: Mapped[ModelCallId] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[TaskId] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    request_sha256: Mapped[ModelRequestHash] = mapped_column(Text, nullable=False)
    occurrence: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[ModelId] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    job_id: Mapped[ModelBatchJobId | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_batch_jobs.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Fernet ciphertext of the canonical ModelRequest / ModelOutcome JSON.
    request_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    outcome_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
