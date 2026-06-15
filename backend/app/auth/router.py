from __future__ import annotations

import secrets
import time
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth.dependencies import auth_service_dep, get_current_user
from app.auth.errors import AuthError
from app.auth.schemas import UserProfile
from app.auth.service import (
    OAUTH_SCOPES,
    AuthService,
    AuthSession,
)
from app.config import settings
from app.models.user import User


router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

_is_secure = settings.FRONTEND_URL.scheme == "https"
_SAMESITE: Literal["lax", "strict", "none"] = "lax"

_ACCESS_MAX_AGE = 30 * 60
_REFRESH_MAX_AGE = 7 * 86400
_OAUTH_STATE_MAX_AGE = 10 * 60


@router.get("/login")
async def login(_request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    oauth_params: dict[str, str] = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": str(settings.GOOGLE_REDIRECT_URI),
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    response = RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{urlencode(oauth_params)}")
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        secure=_is_secure,
        max_age=_OAUTH_STATE_MAX_AGE,
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
    redirect = RedirectResponse(url=settings.frontend_origin, status_code=status.HTTP_302_FOUND)
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
    return UserProfile.from_user(current_user)


def _set_auth_cookies(response: Response, session: AuthSession) -> None:
    domain = settings.COOKIE_DOMAIN
    if domain is not None:
        response.delete_cookie(
            key="access_token", httponly=True, samesite=_SAMESITE, secure=_is_secure, path="/"
        )
        response.delete_cookie(
            key="refresh_token", httponly=True, samesite=_SAMESITE, secure=_is_secure, path="/"
        )
        response.delete_cookie(
            key="session_exp", httponly=False, samesite=_SAMESITE, secure=_is_secure, path="/"
        )
    response.set_cookie(
        key="access_token",
        value=session.jwt_access,
        max_age=_ACCESS_MAX_AGE,
        httponly=True,
        samesite=_SAMESITE,
        secure=_is_secure,
        path="/",
        domain=domain,
    )
    response.set_cookie(
        key="refresh_token",
        value=session.jwt_refresh,
        max_age=_REFRESH_MAX_AGE,
        httponly=True,
        samesite=_SAMESITE,
        secure=_is_secure,
        path="/",
        domain=domain,
    )
    response.set_cookie(
        key="session_exp",
        value=str(int(time.time() * 1000) + _ACCESS_MAX_AGE * 1000),
        max_age=_REFRESH_MAX_AGE,
        httponly=False,
        samesite=_SAMESITE,
        secure=_is_secure,
        path="/",
        domain=domain,
    )


def _clear_auth_cookies(response: Response) -> None:
    domains: list[str | None] = [None]
    if settings.COOKIE_DOMAIN is not None:
        domains.append(settings.COOKIE_DOMAIN)
    for domain in domains:
        for key in ("access_token", "refresh_token"):
            response.delete_cookie(
                key=key,
                httponly=True,
                samesite=_SAMESITE,
                secure=_is_secure,
                path="/",
                domain=domain,
            )
        response.delete_cookie(
            key="session_exp",
            httponly=False,
            samesite=_SAMESITE,
            secure=_is_secure,
            path="/",
            domain=domain,
        )


def _error_redirect(error_key: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"{settings.frontend_origin}/login?error={error_key}",
        status_code=status.HTTP_302_FOUND,
    )
