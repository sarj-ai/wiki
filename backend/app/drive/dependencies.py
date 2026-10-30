from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Response, status
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, http_client_dep
from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    GoogleTokenUnavailableError,
)
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.database import get_db
from app.drive.service import DriveService, GoogleDriveService
from app.identifiers import DriveId
from app.models.user import User


async def drive_service_dep(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
    response: Response,
) -> DriveService:
    try:
        prepared = await GoogleTokenManager(db, http).prepare(user)
        # Auth and credential reads open a transaction. Return its connection
        # before provider I/O or a second cache session can exhaust the pool.
        # Request sessions use expire_on_commit=False, preserving this identity.
        await db.commit()
    except GoogleReauthenticationRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Google login required"
        ) from exc
    except GoogleTokenUnavailableError as exc:
        if exc.retry_after is not None:
            response.headers["Retry-After"] = str(exc.retry_after)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google authorization is temporarily unavailable",
        ) from exc
    return GoogleDriveService(prepared.user, cache, refresh_handler=prepared.refresh_handler)


async def require_member_drive(
    drive_id: DriveId,
    drive: Annotated[DriveService, Depends(drive_service_dep)],
) -> DriveId:
    drives = await drive.list_drives()
    if not any(d.id == drive_id for d in drives):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drive not found")
    return drive_id
