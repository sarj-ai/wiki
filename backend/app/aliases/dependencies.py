from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.service import AliasService, PostgresAliasService
from app.auth.dependencies import get_current_user
from app.database import get_db
from app.files.dependencies import files_service_dep
from app.files.service import FilesService
from app.models.user import User


def alias_service_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    user: Annotated[User, Depends(get_current_user)],
) -> AliasService:
    return PostgresAliasService(db=db, files=files, user=user)
