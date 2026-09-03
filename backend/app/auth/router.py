from __future__ import annotations

import secrets
import time
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, Security, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import APIKeyCookie

from app.auth.dependencies import auth_service_dep, get_current_user, optional_access_token
from app.auth.errors import AuthError
from app.auth.jwt import ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS
from app.auth.schemas import UserProfile
from app.auth.service import (
    OAUTH_SCOPES,
    AuthService,
    AuthSession,
)
from app.config import settings
from app.models.user import User
from app.openapi import authentication_responses


router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

_is_secure = settings.FRONTEND_URL.scheme == "https"
_SAMESITE: Literal["lax", "strict", "none"] = "lax"

_ACCESS_MAX_AGE = ACCESS_TOKEN_EXPIRE_MINUTES * 60
_REFRESH_MAX_AGE = REFRESH_TOKEN_EXPIRE_DAYS * 86400
_OAUTH_STATE_MAX_AGE = 10 * 60
_OAUTH_STATE_COOKIE_PATH = "/auth/callback"
_refresh_cookie = APIKeyCookie(
    name="refresh_token",
    scheme_name="RefreshCookie",
    auto_error=False,
)


@router.get("/login", include_in_schema=False)
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
    response.delete_cookie(key="oauth_state", path="/")
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        secure=_is_secure,
        max_age=_OAUTH_STATE_MAX_AGE,
        path=_OAUTH_STATE_COOKIE_PATH,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback", include_in_schema=False)
async def callback(
    request: Request,
    code: Annotated[str, Query(min_length=1, max_length=4096)],
    state: Annotated[str, Query(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")],
    auth: Annotated[AuthService, Depends(auth_service_dep)],
) -> RedirectResponse:
    expected_state = request.cookies.get("oauth_state")
    try:
        session = await auth.exchange_code_for_session(code, state, expected_state)
    except AuthError as e:
        redirect = _error_redirect(e.redirect_key)
        redirect.delete_cookie(key="oauth_state", path=_OAUTH_STATE_COOKIE_PATH)
        return redirect
    redirect = RedirectResponse(url=settings.frontend_origin, status_code=status.HTTP_302_FOUND)
    set_auth_cookies(redirect, session)
    redirect.delete_cookie(key="oauth_state", path=_OAUTH_STATE_COOKIE_PATH)
    redirect.headers["Cache-Control"] = "no-store"
    return redirect


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    response_class=JSONResponse,
    operation_id="logout",
    responses={
        200: {
            "description": "Session revoked.",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    }
                }
            },
        }
    },
)
async def logout(
    request: Request,
    auth: Annotated[AuthService, Depends(auth_service_dep)],
) -> JSONResponse:
    token = request.cookies.get("refresh_token") or optional_access_token(request)
    await auth.revoke_session(token)
    response = JSONResponse(content={"message": "logged out"})
    clear_auth_cookies(response)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post(
    "/refresh",
    status_code=status.HTTP_200_OK,
    operation_id="refresh_session",
    response_class=JSONResponse,
    responses=authentication_responses(),
)
async def refresh(
    auth: Annotated[AuthService, Depends(auth_service_dep)],
    refresh_token: Annotated[str | None, Security(_refresh_cookie)],
) -> JSONResponse:
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
    set_auth_cookies(response, session)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get(
    "/me",
    status_code=status.HTTP_200_OK,
    operation_id="get_current_user",
    responses=authentication_responses(),
)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> UserProfile:
    return UserProfile.from_user(current_user)


def set_auth_cookies(response: Response, session: AuthSession) -> None:
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


def clear_auth_cookies(response: Response) -> None:
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
    response = RedirectResponse(
        url=f"{settings.frontend_origin}/login?error={error_key}",
        status_code=status.HTTP_302_FOUND,
    )
    response.headers["Cache-Control"] = "no-store"
    return response
