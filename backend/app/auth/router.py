import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.encryption import encrypt_token
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.config import settings
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# Scopes requested during OAuth
OAUTH_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive",
]

# Cookie settings
_is_secure = settings.FRONTEND_URL.startswith("https")


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set HTTP-only auth cookies on the response."""
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=_is_secure,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        samesite="lax",
        secure=_is_secure,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    """Delete auth cookies from the response."""
    response.delete_cookie(key="access_token", path="/")
    response.delete_cookie(key="refresh_token", path="/")


@router.get("/login")
async def login() -> RedirectResponse:
    """Redirect the user to Google's OAuth consent screen."""
    params = urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(OAUTH_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{params}")


@router.get("/callback")
async def callback(
    code: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Handle the Google OAuth callback: exchange code, upsert user, set cookies."""

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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to exchange authorization code",
        )

    token_data = token_response.json()
    google_access_token: str = token_data["access_token"]
    google_refresh_token: str | None = token_data.get("refresh_token")
    expires_in: int | None = token_data.get("expires_in")

    # 2. Fetch user info from Google
    async with httpx.AsyncClient() as client:
        userinfo_response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )

    if userinfo_response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to fetch user info from Google",
        )

    userinfo = userinfo_response.json()
    email: str = userinfo["email"]
    google_id: str = userinfo["id"]
    display_name: str | None = userinfo.get("name")
    avatar_url: str | None = userinfo.get("picture")

    # 3. Domain restriction — strictly enforced server-side
    email_domain = email.split("@")[-1]
    if email_domain != settings.ALLOWED_DOMAIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only @{settings.ALLOWED_DOMAIN} accounts are allowed",
        )

    # 4. Encrypt Google tokens before storage
    encrypted_access = encrypt_token(google_access_token)
    encrypted_refresh = encrypt_token(google_refresh_token) if google_refresh_token else None

    token_expiry = None
    if expires_in is not None:
        token_expiry = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expires_in)

    # 5. Upsert user in database
    result = await db.execute(select(User).where(User.google_id == google_id))
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
        user.email = email
        user.display_name = display_name
        user.avatar_url = avatar_url
        user.encrypted_access_token = encrypted_access
        if encrypted_refresh is not None:
            user.encrypted_refresh_token = encrypted_refresh
        user.token_expiry = token_expiry

    await db.commit()
    await db.refresh(user)

    # 6. Issue our own JWT tokens
    jwt_access = create_access_token(user_id=str(user.id), email=user.email)
    jwt_refresh = create_refresh_token(user_id=str(user.id))

    # 7. Set cookies and redirect to frontend
    redirect = RedirectResponse(url=settings.FRONTEND_URL, status_code=status.HTTP_302_FOUND)
    _set_auth_cookies(redirect, jwt_access, jwt_refresh)
    return redirect


@router.post("/logout")
async def logout() -> Response:
    """Clear auth cookies and log the user out."""
    response = Response(
        content='{"message":"logged out"}',
        media_type="application/json",
    )
    _clear_auth_cookies(response)
    return response


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> dict:
    """Return the currently authenticated user's profile."""
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "display_name": current_user.display_name,
        "avatar_url": current_user.avatar_url,
    }


@router.post("/refresh")
async def refresh(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Use the refresh token cookie to issue a new access token."""
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided",
        )

    payload = decode_token(refresh_token)

    # Ensure this is actually a refresh token
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type",
        )

    user_id = payload["sub"]

    # Verify user still exists
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # Issue a new access token
    new_access = create_access_token(user_id=str(user.id), email=user.email)

    response = Response(
        content='{"message":"refreshed"}',
        media_type="application/json",
    )
    response.set_cookie(
        key="access_token",
        value=new_access,
        httponly=True,
        samesite="lax",
        secure=_is_secure,
        path="/",
    )
    return response
