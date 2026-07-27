from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import secrets
from typing import override
import uuid

import httpx
from loguru import logger
from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption import decrypt_text, encrypt_text
from app.auth.errors import (
    AuthError,
    AuthFailedError,
    DomainRestrictedError,
    InvalidTokenError,
    TokenRevokedError,
)
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.auth.schemas import GoogleTokenResponse, GoogleUserInfo
from app.config import settings
from app.drive import drives_cache
from app.models.user import User


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

OAUTH_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive",
)


@dataclass(frozen=True, slots=True)
class AuthSession:
    jwt_access: str
    jwt_refresh: str


class AuthService(ABC):
    @abstractmethod
    async def exchange_code_for_session(
        self, code: str, state: str, expected_state: str | None
    ) -> AuthSession: ...

    @abstractmethod
    async def refresh_session(self, refresh_token: str) -> AuthSession: ...

    @abstractmethod
    async def revoke_session(self, access_token: str | None) -> None: ...

    @abstractmethod
    async def resolve_current_user(self, jwt_token: str) -> User: ...


class GoogleAuthService(AuthService):
    def __init__(self, db: AsyncSession, http_client: httpx.AsyncClient) -> None:
        self._db = db
        self._http = http_client

    @override
    async def exchange_code_for_session(
        self, code: str, state: str, expected_state: str | None
    ) -> AuthSession:
        if not expected_state or not secrets.compare_digest(state, expected_state):
            logger.warning("auth: OAuth state mismatch (possible CSRF)")
            msg = "OAuth state mismatch"
            raise AuthFailedError(msg)
        google_tokens = await self._exchange_google_code(code)
        userinfo = await self._fetch_userinfo(google_tokens.access_token)
        self._enforce_domain(userinfo.email)
        try:
            user = await self._upsert_user(userinfo, google_tokens)
        except Exception as e:
            logger.exception("Auth callback failed for {email}", email=userinfo.email)
            raise AuthError from e
        return self._issue_session(user)

    @override
    async def refresh_session(self, refresh_token: str) -> AuthSession:
        payload = decode_token(refresh_token)
        if payload.type != "refresh":
            msg = "Invalid token type"
            raise TokenRevokedError(msg)
        user = await self._load_user(payload.sub)
        if user is None:
            msg = "User not found"
            raise TokenRevokedError(msg)
        if payload.tv != user.token_version:
            logger.warning("auth: revoked refresh token presented user={user_id}", user_id=user.id)
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        return self._issue_session(user)

    @override
    async def revoke_session(self, access_token: str | None) -> None:
        if access_token is None:
            return
        user: User | None = None
        try:
            user = await self._revokable_access_user(access_token)
            if user is None:
                return
            await self._revoke_user_session(user)
        except Exception:  # ruff: ignore[blind-except] — logout proceeds even if DB bump fails; cookies still clear.
            logger.opt(exception=True).warning(
                "logout: failed to bump token_version; proceeding to clear cookies"
            )
            return
        drives_cache.invalidate(str(user.id))

    @override
    async def resolve_current_user(self, jwt_token: str) -> User:
        payload = decode_token(jwt_token)
        if payload.type != "access":
            msg = "Invalid token type"
            raise InvalidTokenError(msg)
        user = await self._load_user(payload.sub)
        if user is None:
            msg = "User not found"
            raise TokenRevokedError(msg)
        if payload.tv != user.token_version:
            logger.warning("auth: revoked access token presented user={user_id}", user_id=user.id)
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        return user

    async def _revoke_google_token(self, user: User) -> None:
        if user.encrypted_refresh_token is None:
            return
        try:
            refresh_token = decrypt_text(user.encrypted_refresh_token)
            await self._http.post(GOOGLE_REVOKE_URL, data={"token": refresh_token})
        except Exception:  # ruff: ignore[blind-except] — best-effort; logout proceeds regardless.
            logger.opt(exception=True).warning(
                "logout: Google token revocation failed user={user_id}", user_id=user.id
            )

    async def _revokable_access_user(self, access_token: str) -> User | None:
        payload = decode_token(access_token)
        if payload.type != "access":
            return None
        return await self._load_user(payload.sub)

    async def _revoke_user_session(self, user: User) -> None:
        user.token_version += 1
        await self._db.commit()
        await self._revoke_google_token(user)

    async def _exchange_google_code(self, code: str) -> _GoogleTokens:
        resp = await self._http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
                "redirect_uri": str(settings.GOOGLE_REDIRECT_URI),
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            logger.warning(
                "Google token exchange failed: {status_code}", status_code=resp.status_code
            )
            raise AuthFailedError
        data = GoogleTokenResponse.model_validate(resp.json())
        return _GoogleTokens(
            access_token=data.access_token,
            refresh_token=data.refresh_token,
        )

    async def _fetch_userinfo(self, google_access_token: str) -> _Userinfo:
        resp = await self._http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )
        if resp.status_code != 200:
            raise AuthFailedError
        data = GoogleUserInfo.model_validate(resp.json())
        return _Userinfo(
            email=data.email.lower(),
            google_id=data.id,
            display_name=data.name,
            avatar_url=data.picture,
        )

    def _enforce_domain(self, email: str) -> None:
        parts = email.rsplit("@", 1)
        if len(parts) != 2 or parts[1] != settings.ALLOWED_DOMAIN:
            logger.warning(
                "auth: domain-restricted login rejected domain={domain}",
                domain=parts[1] if len(parts) == 2 else "<malformed>",
            )
            raise DomainRestrictedError

    async def _upsert_user(self, info: _Userinfo, tokens: _GoogleTokens) -> User:
        encrypted_access = encrypt_text(tokens.access_token)
        encrypted_refresh = encrypt_text(tokens.refresh_token) if tokens.refresh_token else None

        user = await self._find_existing_user(google_id=info.google_id, email=info.email)
        if user is None:
            user = User(
                email=info.email,
                google_id=info.google_id,
                display_name=info.display_name,
                avatar_url=info.avatar_url,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
            )
            self._db.add(user)
        else:
            user.google_id = info.google_id
            user.email = info.email
            user.display_name = info.display_name
            user.avatar_url = info.avatar_url
            user.encrypted_access_token = encrypted_access
            if encrypted_refresh is not None:
                user.encrypted_refresh_token = encrypted_refresh

        await self._db.commit()
        await self._db.refresh(user)
        return user

    async def _find_existing_user(self, *, google_id: str, email: str) -> User | None:
        stmt = (
            select(User)
            .where(or_(User.google_id == google_id, User.email == email))
            .order_by(case((User.google_id == google_id, 0), else_=1))
            .limit(1)
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def _load_user(self, user_id: uuid.UUID) -> User | None:
        result = await self._db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    def _issue_session(self, user: User) -> AuthSession:
        access = create_access_token(
            user_id=user.id, email=user.email, token_version=user.token_version
        )
        refresh = create_refresh_token(user_id=user.id, token_version=user.token_version)
        return AuthSession(jwt_access=access, jwt_refresh=refresh)


@dataclass(frozen=True, slots=True)
class _GoogleTokens:
    access_token: str
    refresh_token: str | None


@dataclass(frozen=True, slots=True)
class _Userinfo:
    email: str
    google_id: str
    display_name: str | None
    avatar_url: str | None
