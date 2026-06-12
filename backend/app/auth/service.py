"""Authentication service: OAuth exchange, session issuance, revocation.

Lives behind an ABC so tests can swap in a fake service without reaching
for monkeypatching. The HTTP-response shaping (cookies, redirect URLs)
stays in the router — this module only handles the business logic:
talking to Google, upserting users, issuing JWTs, validating session
JWTs, incrementing ``token_version`` on logout.

Error semantics
---------------

Every failure path raises a subclass of :class:`AuthError` with a
``redirect_key`` attribute that the callback router uses to pick the
``?error=...`` value for the frontend redirect. Other entry points
(refresh, current-user resolution) translate :class:`AuthError` to
HTTP 401 at the app boundary.
"""

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

from app.auth.encryption import encrypt_text
from app.auth.errors import (
    AuthError,
    AuthFailedError,
    DomainRestrictedError,
    InvalidTokenError,
    TokenRevokedError,
)
from app.auth.jwt import create_access_token, create_refresh_token, decode_token
from app.config import settings
from app.drive import drives_cache
from app.models.user import User


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 — public OAuth endpoint
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

OAUTH_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive",
)


@dataclass(frozen=True, slots=True)
class AuthSession:
    """Tokens issued after a successful OAuth exchange or refresh."""

    jwt_access: str
    jwt_refresh: str


class AuthService(ABC):
    """Contract for OAuth callback handling and JWT lifecycle."""

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
    """Production ``AuthService`` backed by Google OAuth + our users table."""

    def __init__(self, db: AsyncSession, http_client: httpx.AsyncClient) -> None:
        self._db = db
        self._http = http_client

    @override
    async def exchange_code_for_session(
        self, code: str, state: str, expected_state: str | None
    ) -> AuthSession:
        # CSRF guard: the cookie ``oauth_state`` was minted by /auth/login
        # and must round-trip via Google in the ``state`` query param.
        if not expected_state or not secrets.compare_digest(state, expected_state):
            raise AuthFailedError("OAuth state mismatch")
        google_tokens = await self._exchange_google_code(code)
        userinfo = await self._fetch_userinfo(google_tokens.access_token)
        self._enforce_domain(userinfo.email)
        try:
            user = await self._upsert_user(userinfo, google_tokens)
        except Exception as e:
            logger.exception("Auth callback failed for {}", userinfo.email)
            raise AuthError from e
        return self._issue_session(user)

    @override
    async def refresh_session(self, refresh_token: str) -> AuthSession:
        payload = decode_token(refresh_token)
        if payload.get("type") != "refresh":
            raise TokenRevokedError("Invalid token type")
        user_id = payload.get("sub")
        if not user_id:
            raise TokenRevokedError("Malformed refresh token")
        user = await self._load_user(uuid.UUID(user_id))
        if user is None:
            raise TokenRevokedError("User not found")
        token_tv = payload.get("tv", 0)
        if token_tv != user.token_version:
            raise TokenRevokedError("Token has been revoked")
        return self._issue_session(user)

    @override
    async def revoke_session(self, access_token: str | None) -> None:
        if access_token is None:
            return
        try:
            payload = decode_token(access_token)
            if payload.get("type") != "access":
                return
            user_id = payload.get("sub")
            if not user_id:
                return
            user = await self._load_user(uuid.UUID(user_id))
            if user is None:
                return
            user.token_version += 1
            await self._db.commit()
            # Drop the user's cached drives.list so the next session
            # starts cold instead of replaying a possibly-stale list.
            drives_cache.invalidate(str(user.id))
        except Exception:  # noqa: BLE001 — logout proceeds even if DB bump fails; cookies still clear.
            logger.opt(exception=True).warning(
                "logout: failed to bump token_version; proceeding to clear cookies"
            )

    @override
    async def resolve_current_user(self, jwt_token: str) -> User:
        payload = decode_token(jwt_token)
        if payload.get("type") != "access":
            raise InvalidTokenError("Invalid token type")
        user_id = payload["sub"]
        user = await self._load_user(uuid.UUID(user_id))
        if user is None:
            raise TokenRevokedError("User not found")
        token_tv = payload.get("tv", 0)
        if token_tv != user.token_version:
            raise TokenRevokedError("Token has been revoked")
        return user

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _exchange_google_code(self, code: str) -> _GoogleTokens:
        resp = await self._http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            logger.warning("Google token exchange failed: {}", resp.status_code)
            raise AuthFailedError
        data = resp.json()
        return _GoogleTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
        )

    async def _fetch_userinfo(self, google_access_token: str) -> _Userinfo:
        resp = await self._http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )
        if resp.status_code != 200:
            raise AuthFailedError
        data = resp.json()
        # Normalize at the boundary: ``users.email`` is Postgres
        # ``citext``, but lowercasing here keeps logs, JWT claims, and
        # any downstream string-equality comparisons in one casing.
        return _Userinfo(
            email=data["email"].lower(),
            google_id=data["id"],
            display_name=data.get("name"),
            avatar_url=data.get("picture"),
        )

    def _enforce_domain(self, email: str) -> None:
        parts = email.rsplit("@", 1)
        if len(parts) != 2 or parts[1] != settings.ALLOWED_DOMAIN:
            raise DomainRestrictedError

    async def _upsert_user(self, info: _Userinfo, tokens: _GoogleTokens) -> User:
        encrypted_access = encrypt_text(tokens.access_token)
        encrypted_refresh = encrypt_text(tokens.refresh_token) if tokens.refresh_token else None

        # Look up by google_id first, then by email (stub users from share/search
        # flows may exist with a different google_id from Admin Directory API).
        user = await self._find_existing_user(info.google_id, info.email)
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

    async def _find_existing_user(self, google_id: str, email: str) -> User | None:
        # One round-trip OR-match. ORDER BY prefers a google_id hit over an
        # email-only hit so existing users keep stable account linkage when
        # they later log in with a Workspace alias.
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
            user_id=str(user.id), email=user.email, token_version=user.token_version
        )
        refresh = create_refresh_token(user_id=str(user.id), token_version=user.token_version)
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
