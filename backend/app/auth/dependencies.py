"""FastAPI dependencies for auth: current-user resolution + AuthService wiring."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import decode_token
from app.auth.service import AuthService, GoogleAuthService
from app.database import get_db
from app.models.user import User


async def http_client_dep() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


def auth_service_dep(
    db: AsyncSession = Depends(get_db),
    http: httpx.AsyncClient = Depends(http_client_dep),
) -> AuthService:
    return GoogleAuthService(db=db, http_client=http)


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _extract_token(request)
    payload = decode_token(token)

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload["sub"]
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    token_tv = payload.get("tv", 0)
    if token_tv != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    return user


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
