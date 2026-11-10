from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.aliases.service import AliasService, PostgresAliasService
from app.database import get_db
from app.drive.dependencies import drive_service_dep
from app.drive.service import DriveService
from app.files.dependencies import files_service_dep
from app.files.service import FilesService


async def alias_service_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    files: Annotated[FilesService, Depends(files_service_dep)],
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> AliasService:
    return PostgresAliasService(db=db, files=files, drive=drive)
