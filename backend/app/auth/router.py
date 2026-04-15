import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.encryption import encrypt_token
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.workspaces.service import sync_all_groups, sync_folder_permissions, sync_user_memberships

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public OAuth endpoint, not a credential
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

OAUTH_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive",
]

_is_secure = settings.FRONTEND_URL.startswith("https")
_SAMESITE: Literal["lax", "strict", "none"] = "none" if _is_secure else "lax"

# SameSite=none is required for cross-origin cookie auth (frontend ≠ backend domain).
# In local dev (HTTP), use lax since browsers reject SameSite=none without Secure.


class UserProfile(BaseModel):
    id: str
    email: str
    display_name: str | None
    avatar_url: str | None


_ACCESS_MAX_AGE = 30 * 60  # 30 minutes (matches JWT expiry)
_REFRESH_MAX_AGE = 7 * 86400  # 7 days (matches JWT expiry)


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=access_token,
        max_age=_ACCESS_MAX_AGE,
        httponly=True,
        samesite=_SAMESITE,
        secure=_is_secure,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
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


def _validate_email_domain(email: str) -> None:
    """Enforce @sarj.ai domain restriction. Raises HTTPException(403) on failure."""
    parts = email.rsplit("@", 1)
    if len(parts) != 2 or parts[1] != settings.ALLOWED_DOMAIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only @{settings.ALLOWED_DOMAIN} accounts are allowed",
        )


@router.get("/login")
async def login(_request: Request) -> RedirectResponse:
    # Generate CSRF state token and store in a short-lived cookie
    state = secrets.token_urlsafe(32)

    # Let Google handle the consent screen automatically.
    # For internal Workspace apps, Google only shows consent on first authorization.
    # We keep access_type=offline so Google returns a refresh token on first login.
    # Returning users won't get a new refresh token, but we already have one stored.
    oauth_params: dict[str, str] = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(OAUTH_SCOPES),
        "access_type": "offline",
        "state": state,
    }

    params = urlencode(oauth_params)
    response = RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{params}")
    response.set_cookie(
        key="oauth_state",
        value=state,
        httponly=True,
        samesite="lax",
        secure=_is_secure,
        max_age=600,  # 10 minutes
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    def _error_redirect(error: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/login?error={error}",
            status_code=status.HTTP_302_FOUND,
        )

    # Verify CSRF state parameter matches the cookie
    expected_state = request.cookies.get("oauth_state")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        return _error_redirect("auth_failed")

    # 1. Exchange authorization code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )

        if token_response.status_code != 200:
            log.warning("Google token exchange failed: %s", token_response.status_code)
            return _error_redirect("auth_failed")

        token_data = token_response.json()
        google_access_token: str = token_data["access_token"]
        google_refresh_token: str | None = token_data.get("refresh_token")
        expires_in: int | None = token_data.get("expires_in")

        # 2. Fetch user info (reuse the same httpx client)
        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )

    if userinfo_response.status_code != 200:
        return _error_redirect("auth_failed")

    userinfo = userinfo_response.json()
    email: str = userinfo["email"]
    google_id: str = userinfo["id"]
    display_name: str | None = userinfo.get("name")
    avatar_url: str | None = userinfo.get("picture")

    # 3. Domain restriction — strictly enforced server-side
    parts = email.rsplit("@", 1)
    if len(parts) != 2 or parts[1] != settings.ALLOWED_DOMAIN:
        return _error_redirect("domain_restricted")

    try:
        # 4. Encrypt Google tokens before storage
        encrypted_access = encrypt_token(google_access_token)
        encrypted_refresh = encrypt_token(google_refresh_token) if google_refresh_token else None

        token_expiry = None
        if expires_in is not None:
            token_expiry = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=expires_in)

        # 5. Upsert user in database
        # Look up by google_id first, then by email (stub users from share/search
        # may exist with a different google_id from the Admin Directory API).
        result = await db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()

        if user is None:
            result = await db.execute(select(User).where(User.email == email))
            user = result.scalar_one_or_none()

        if user is None:
            user = User(
                email=email,
                google_id=google_id,
                display_name=display_name,
                avatar_url=avatar_url,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                token_expiry=token_expiry,
            )
            db.add(user)
        else:
            user.google_id = google_id
            user.email = email
            user.display_name = display_name
            user.avatar_url = avatar_url
            user.encrypted_access_token = encrypted_access
            if encrypted_refresh is not None:
                user.encrypted_refresh_token = encrypted_refresh
            user.token_expiry = token_expiry

        await db.commit()
        await db.refresh(user)

        # 6. Sync workspaces and memberships (best-effort, don't block login)
        try:
            await sync_all_groups(db)
            await sync_user_memberships(db, user)
        except Exception:
            log.warning("Failed to sync workspaces for %s", email, exc_info=True)

        # 7. Scan folder permissions in background (slow — don't block login)
        _bg_user_id = user.id

        async def _bg_folder_sync() -> None:
            from app.database import async_session as bg_session

            try:
                async with bg_session() as s:
                    await sync_folder_permissions(s)
                    result = await s.execute(select(User).where(User.id == _bg_user_id))
                    bg_user = result.scalar_one_or_none()
                    if bg_user:
                        await sync_user_memberships(s, bg_user)
            except Exception:
                log.warning("Background folder permission sync failed", exc_info=True)

        background_tasks.add_task(_bg_folder_sync)

        # 8. Issue our own JWT tokens (embed token_version for revocation)
        jwt_access = create_access_token(
            user_id=str(user.id), email=user.email, token_version=user.token_version
        )
        jwt_refresh = create_refresh_token(user_id=str(user.id), token_version=user.token_version)

        # 9. Set cookies and redirect to frontend (clear the oauth_state cookie)
        redirect = RedirectResponse(url=settings.FRONTEND_URL, status_code=status.HTTP_302_FOUND)
        _set_auth_cookies(redirect, jwt_access, jwt_refresh)
        redirect.delete_cookie(key="oauth_state", path="/")
        return redirect

    except Exception:
        log.exception("Auth callback failed for %s", email)
        return _error_redirect("server_error")


@router.post("/logout")
async def logout(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    # Increment token_version to invalidate all existing JWTs
    token = request.cookies.get("access_token")
    if token:
        try:
            payload = decode_token(token)
            if payload.get("type") != "access":
                raise ValueError("wrong token type")
            user_id = payload.get("sub")
            if user_id:
                result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
                user = result.scalar_one_or_none()
                if user:
                    user.token_version += 1
                    await db.commit()
        except Exception:
            log.warning(
                "logout: failed to bump token_version; proceeding to clear cookies", exc_info=True
            )

    response = JSONResponse(content={"message": "logged out"})
    _clear_auth_cookies(response)
    return response


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> UserProfile:
    return UserProfile(
        id=str(current_user.id),
        email=current_user.email,
        display_name=current_user.display_name,
        avatar_url=current_user.avatar_url,
    )


@router.post("/refresh")
async def refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided",
        )

    payload = decode_token(refresh_token)

    if payload.get("type") != "refresh":
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

    # Check token_version — reject tokens issued before logout
    token_tv = payload.get("tv", 0)
    if token_tv != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )

    new_access = create_access_token(
        user_id=str(user.id), email=user.email, token_version=user.token_version
    )
    new_refresh = create_refresh_token(user_id=str(user.id), token_version=user.token_version)

    response = JSONResponse(content={"message": "refreshed"})
    _set_auth_cookies(response, new_access, new_refresh)
    return response
