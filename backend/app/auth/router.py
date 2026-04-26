"""HTTP surface for auth: OAuth redirect, callback, refresh, logout, /me.

The router is thin — it handles cookies and redirects; all OAuth and
session logic (including OAuth-state CSRF validation) lives in
``AuthService``.
"""

from __future__ import annotations

import secrets
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.auth.dependencies import auth_service_dep, get_current_user
from app.auth.errors import AuthError
from app.auth.service import (
    OAUTH_SCOPES,
    AuthService,
    AuthSession,
)
from app.config import settings
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

_is_secure = settings.FRONTEND_URL.startswith("https")
# SameSite=none is required for cross-origin cookie auth (frontend ≠ backend
# domain). In local dev (HTTP), use lax — browsers reject SameSite=none on
# non-secure cookies.
_SAMESITE: Literal["lax", "strict", "none"] = "none" if _is_secure else "lax"

_ACCESS_MAX_AGE = 30 * 60  # 30 minutes (matches JWT expiry)
_REFRESH_MAX_AGE = 7 * 86400  # 7 days (matches JWT expiry)


class UserProfile(BaseModel):
    id: str
    email: str
    display_name: str | None
    avatar_url: str | None


@router.get("/login")
async def login(_request: Request) -> RedirectResponse:
    # Generate a CSRF state token and stash it in a short-lived cookie so
    # we can verify the callback came from our own redirect.
    state = secrets.token_urlsafe(32)
    oauth_params: dict[str, str] = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "state": state,
    }
    response = RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(oauth_params)}")
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        secure=_is_secure,
        max_age=600,
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
    auth: AuthService = Depends(auth_service_dep),
) -> RedirectResponse:
    expected_state = request.cookies.get("oauth_state")
    try:
        session = await auth.exchange_code_for_session(code, state, expected_state)
    except AuthError as e:
        return _error_redirect(e.redirect_key)
    redirect = RedirectResponse(url=settings.FRONTEND_URL, status_code=status.HTTP_302_FOUND)
    _set_auth_cookies(redirect, session)
    redirect.delete_cookie(key="oauth_state", path="/")
    return redirect


@router.post("/logout")
async def logout(
    request: Request,
    auth: AuthService = Depends(auth_service_dep),
) -> JSONResponse:
    await auth.revoke_session(request.cookies.get("access_token"))
    response = JSONResponse(content={"message": "logged out"})
    _clear_auth_cookies(response)
    return response


@router.post("/refresh")
async def refresh(
    request: Request,
    auth: AuthService = Depends(auth_service_dep),
) -> JSONResponse:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided",
        )
    try:
        session = await auth.refresh_session(refresh_token)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e) or "Token has been revoked",
        ) from e
    response = JSONResponse(content={"message": "refreshed"})
    _set_auth_cookies(response, session)
    return response


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> UserProfile:
    return UserProfile(
        id=str(current_user.id),
        email=current_user.email,
        display_name=current_user.display_name,
        avatar_url=current_user.avatar_url,
    )


def _set_auth_cookies(response: Response, session: AuthSession) -> None:
    response.set_cookie(
        key="access_token",
        value=session.jwt_access,
        max_age=_ACCESS_MAX_AGE,
        httponly=True,
        samesite=_SAMESITE,
        secure=_is_secure,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=session.jwt_refresh,
        max_age=_REFRESH_MAX_AGE,
        httponly=True,
        samesite=_SAMESITE,
        secure=_is_secure,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key="access_token", httponly=True, samesite=_SAMESITE, secure=_is_secure, path="/"
    )
    response.delete_cookie(
        key="refresh_token", httponly=True, samesite=_SAMESITE, secure=_is_secure, path="/"
    )


def _error_redirect(error_key: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/login?error={error_key}",
        status_code=status.HTTP_302_FOUND,
    )
