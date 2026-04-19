"""FastAPI dependency wiring for ``ExtrasService``."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.extras.service import ExtrasService, PostgresExtrasService
from app.models.user import User


def extras_service_dep(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ExtrasService:
    return PostgresExtrasService(db=db, user=user)
