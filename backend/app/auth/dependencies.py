from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import AuthError
from app.auth.service import AuthService, GoogleAuthService
from app.cache.dependencies import cache_service_dep
from app.cache.service import CacheService
from app.database import get_db
from app.models.user import User
from app.performance import timed


_access_cookie = APIKeyCookie(name="access_token", auto_error=False)
_bearer_token = HTTPBearer(auto_error=False)


async def http_client_dep(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


async def auth_service_dep(
    db: Annotated[AsyncSession, Depends(get_db)],
    http: Annotated[httpx.AsyncClient, Depends(http_client_dep)],
    cache: Annotated[CacheService, Depends(cache_service_dep)],
) -> AuthService:
    return GoogleAuthService(db=db, http_client=http, cache=cache)


async def get_current_user(
    auth: Annotated[AuthService, Depends(auth_service_dep)],
    cookie_token: Annotated[str | None, Security(_access_cookie)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer_token)],
) -> User:
    token = cookie_token or (bearer.credentials if bearer is not None else None)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    try:
        with timed("auth"):
            return await auth.resolve_current_user(token)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e) or "Not authenticated",
        ) from e


def optional_access_token(request: Request) -> str | None:
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ")
    return None
