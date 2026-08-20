from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import AuthError
from app.auth.service import AuthService, GoogleAuthService
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.database import get_db
from app.models.user import User


def http_client_dep(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


def auth_service_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
) -> AuthService:
    return GoogleAuthService(db=db, http_client=http, cache=cache)


async def get_current_user(
    request: Request,
    auth: Annotated[AuthService, Depends(auth_service_dep)],
) -> User:
    token = _extract_token(request)
    try:
        return await auth.resolve_current_user(token)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e) or "Not authenticated",
        ) from e


def _extract_token(request: Request) -> str:
    token = optional_access_token(request)
    if token is not None:
        return token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


def optional_access_token(request: Request) -> str | None:
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ")
    return None
