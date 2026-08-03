from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.rate_limits.service import PostgresRateLimitService, RateLimitService


def rate_limit_service_dep(db: Annotated[AsyncSession, Depends(get_db)]) -> RateLimitService:
    return PostgresRateLimitService(db=db)
