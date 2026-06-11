"""FastAPI dependencies for auth: current-user resolution + AuthService wiring."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import AuthError
from app.auth.service import AuthService, GoogleAuthService
from app.database import get_db
from app.models.user import User


def http_client_dep(request: Request) -> httpx.AsyncClient:
    client: httpx.AsyncClient = request.app.state.http_client
    return client


def auth_service_dep(
    db: AsyncSession = Depends(get_db),
    http: httpx.AsyncClient = Depends(http_client_dep),
) -> AuthService:
    return GoogleAuthService(db=db, http_client=http)


async def get_current_user(
    request: Request,
    auth: AuthService = Depends(auth_service_dep),
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
    # Cookie first so browser sessions work seamlessly; Authorization
    # header fallback for non-browser clients.
    token = request.cookies.get("access_token")
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )
