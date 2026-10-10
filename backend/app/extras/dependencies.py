from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.extras.service import ExtrasService, PostgresExtrasService
from app.models.user import User


async def extras_service_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> ExtrasService:
    return PostgresExtrasService(db=db, user=user)
