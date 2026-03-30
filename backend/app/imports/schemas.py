from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: Literal["pending", "processing", "completed", "failed"]
    total_items: int
    processed_items: int
    error_message: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
