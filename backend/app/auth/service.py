from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hmac
import secrets
from typing import NamedTuple, override
import uuid

import httpx
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption import decrypt_text, encrypt_text
from app.auth.errors import (
    AuthError,
    AuthFailedError,
    DomainRestrictedError,
    InvalidTokenError,
    TokenRevokedError,
)
from app.auth.jwt import (
    REFRESH_TOKEN_EXPIRE_DAYS,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.auth.schemas import GoogleTokenResponse, GoogleUserInfo
from app.cache.service import CacheNamespace, CacheService
from app.config import settings
from app.identifiers import (
    AuthSessionFamilyId,
    AuthSessionId,
    GoogleUserId,
    TokenId,
    UserId,
)
from app.models.auth_session import UserSession
from app.models.user import User


GOOGLE_OAUTH_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

OAUTH_SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/drive",
)

_IDENTITY_VERIFICATION_FAILED = "Google account could not be verified"


@dataclass(frozen=True, slots=True)
class AuthSession:
    jwt_access: str
    jwt_refresh: str


class AccessIdentity(NamedTuple):
    session: UserSession
    user: User


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
    def __init__(
        self,
        db: AsyncSession,
        http_client: httpx.AsyncClient,
        cache: CacheService | None = None,
    ) -> None:
        self._db = db
        self._http = http_client
        self._cache = cache

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
            return await self._issue_new_session(user)
        except AuthFailedError:
            raise
        except Exception as e:
            logger.warning("auth: callback failed")
            raise AuthError from e

    @override
    async def refresh_session(self, refresh_token: str) -> AuthSession:
        payload = decode_token(refresh_token)
        if payload.type != "refresh":
            msg = "Invalid token type"
            raise TokenRevokedError(msg)
        session = await self._load_session(payload.sid, for_update=True)
        now = datetime.now(UTC)
        if (
            session is None
            or session.user_id != payload.sub
            or session.revoked_at is not None
            or session.expires_at <= now
        ):
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        if not hmac.compare_digest(str(payload.jti), str(session.refresh_jti)):
            await self._revoke_session_family(session.family_id, now)
            logger.warning("auth: refresh token replay detected")
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        if (user := await self._load_user(payload.sub)) is None:
            msg = "User not found"
            raise TokenRevokedError(msg)
        if not hmac.compare_digest(str(payload.tv), str(user.token_version)):
            logger.warning("auth: revoked refresh token presented")
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        refresh_jti = TokenId(uuid.uuid7())
        session.refresh_jti = refresh_jti
        session.expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        session.updated_at = now
        await self._db.commit()
        return self._encode_session(user, session.id, refresh_jti)

    @override
    async def revoke_session(self, access_token: str | None) -> None:
        if access_token is None:
            return
        user: User | None = None
        revoked = False
        with suppress(Exception):
            payload = decode_token(access_token)
            session = await self._load_session(payload.sid, for_update=True)
            user = await self._load_user(payload.sub)
            if session is None or user is None or session.user_id != user.id:
                return
            await self._revoke_user_session(user, session)
            revoked = True
        if not revoked:
            logger.warning("logout: session revocation failed; proceeding to clear cookies")
        if user is not None and self._cache is not None:
            await self._cache.delete(CacheNamespace.DRIVE_LIST, str(user.id))

    @override
    async def resolve_current_user(self, jwt_token: str) -> User:
        payload = decode_token(jwt_token)
        if payload.type != "access":
            msg = "Invalid token type"
            raise InvalidTokenError(msg)
        if (identity := await self._load_access_identity(payload.sid, payload.sub)) is None:
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        session, user = identity
        if (
            session.user_id != payload.sub
            or session.revoked_at is not None
            or session.expires_at <= datetime.now(UTC)
        ):
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        if not hmac.compare_digest(str(payload.tv), str(user.token_version)):
            logger.warning("auth: revoked access token presented")
            msg = "Token has been revoked"
            raise TokenRevokedError(msg)
        return user

    async def _load_access_identity(
        self,
        session_id: AuthSessionId,
        user_id: UserId,
    ) -> AccessIdentity | None:
        statement = (
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(UserSession.id == session_id, UserSession.user_id == user_id)
        )
        row = (await self._db.execute(statement)).one_or_none()
        return AccessIdentity(session=row[0], user=row[1]) if row is not None else None

    async def _revoke_user_session(self, user: User, session: UserSession) -> None:
        now = datetime.now(UTC)
        refresh_token = (
            decrypt_text(user.encrypted_refresh_token) if user.encrypted_refresh_token else None
        )
        session.revoked_at = now
        session.updated_at = now
        user.token_version += 1
        user.google_token_generation += 1
        user.encrypted_access_token = None
        user.encrypted_refresh_token = None
        user.google_access_token_expires_at = None
        user.google_refresh_lease_owner = None
        user.google_refresh_lease_until = None
        await self._db.commit()
        await self._revoke_google_token(refresh_token)

    async def _revoke_session_family(
        self, family_id: AuthSessionFamilyId, revoked_at: datetime
    ) -> None:
        await self._db.execute(
            update(UserSession)
            .where(UserSession.family_id == family_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at, updated_at=revoked_at)
        )
        await self._db.commit()

    async def _revoke_google_token(self, refresh_token: str | None) -> None:
        if refresh_token is None:
            return
        revoked = False
        with suppress(Exception):
            response = await self._http.post(GOOGLE_REVOKE_URL, data={"token": refresh_token})
            response.raise_for_status()
            revoked = True
        if not revoked:
            logger.warning("logout: Google token revocation failed")

    async def _exchange_google_code(self, code: str) -> GoogleTokens:
        resp = await self._http.post(
            GOOGLE_OAUTH_URL,
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
        data = GoogleTokenResponse.model_validate_json(resp.content)
        return GoogleTokens(
            access_token=data.access_token,
            refresh_token=data.refresh_token,
            expires_at=datetime.now(UTC) + data.expires_in,
        )

    async def _fetch_userinfo(self, google_access_token: str) -> GoogleIdentity:
        resp = await self._http.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )
        if resp.status_code != 200:
            raise AuthFailedError
        try:
            data = GoogleUserInfo.model_validate_json(resp.content)
        except ValidationError as exc:
            logger.warning("auth: Google returned invalid or unverified userinfo")
            raise AuthFailedError(_IDENTITY_VERIFICATION_FAILED) from exc
        return GoogleIdentity(
            email=data.email.lower(),
            google_id=data.id,
            display_name=data.name,
            avatar_url=data.picture,
        )

    def _enforce_domain(self, email: str) -> None:
        parts = email.rsplit("@", 1)
        if len(parts) != 2 or parts[1] != settings.ALLOWED_DOMAIN:
            logger.warning("auth: domain-restricted login rejected")
            raise DomainRestrictedError

    async def _upsert_user(self, info: GoogleIdentity, tokens: GoogleTokens) -> User:
        user = await self._resolve_identity_user(google_id=info.google_id, email=info.email)
        encrypted_access = encrypt_text(tokens.access_token)
        encrypted_refresh = encrypt_text(tokens.refresh_token) if tokens.refresh_token else None
        if user is None:
            user = User(
                email=info.email,
                google_id=info.google_id,
                display_name=info.display_name,
                avatar_url=info.avatar_url,
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                google_access_token_expires_at=tokens.expires_at,
            )
            self._db.add(user)
        else:
            user.email = info.email
            user.display_name = info.display_name
            user.avatar_url = info.avatar_url
            user.encrypted_access_token = encrypted_access
            user.google_access_token_expires_at = tokens.expires_at
            if encrypted_refresh is not None:
                user.encrypted_refresh_token = encrypted_refresh
            user.google_token_generation = (user.google_token_generation or 0) + 1
            user.google_refresh_lease_owner = None
            user.google_refresh_lease_until = None

        await self._db.commit()
        await self._db.refresh(user)
        return user

    async def _resolve_identity_user(self, *, google_id: GoogleUserId, email: str) -> User | None:
        users = await self._find_identity_users(google_id=google_id, email=email)
        subject_user = next((user for user in users if user.google_id == google_id), None)
        email_user = next((user for user in users if user.email == email), None)
        if email_user is not None and (subject_user is None or email_user.id != subject_user.id):
            logger.warning("auth: email is already bound to another Google identity")
            raise AuthFailedError(_IDENTITY_VERIFICATION_FAILED)
        return subject_user

    async def _find_identity_users(self, *, google_id: GoogleUserId, email: str) -> list[User]:
        result = await self._db.execute(
            select(User).where(
                User.id.in_(
                    union_all(
                        select(User.id).where(User.google_id == google_id),
                        select(User.id).where(User.email == email),
                    )
                )
            )
        )
        return list(result.scalars().all())

    async def _load_user(self, user_id: UserId) -> User | None:
        result = await self._db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def _load_session(
        self,
        session_id: AuthSessionId,
        *,
        for_update: bool = False,
    ) -> UserSession | None:
        statement = select(UserSession).where(UserSession.id == session_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._db.execute(statement)
        return result.scalar_one_or_none()

    async def _issue_new_session(self, user: User) -> AuthSession:
        session_id = AuthSessionId(uuid.uuid7())
        refresh_jti = TokenId(uuid.uuid7())
        now = datetime.now(UTC)
        self._db.add(
            UserSession(
                id=session_id,
                family_id=AuthSessionFamilyId(session_id),
                user_id=UserId(user.id),
                refresh_jti=refresh_jti,
                expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
                revoked_at=None,
                created_at=now,
                updated_at=now,
            )
        )
        await self._db.commit()
        return self._encode_session(user, session_id, refresh_jti)

    @staticmethod
    def _encode_session(
        user: User,
        session_id: AuthSessionId,
        refresh_jti: TokenId,
    ) -> AuthSession:
        access = create_access_token(
            user_id=UserId(user.id),
            session_id=session_id,
            token_version=user.token_version,
        )
        refresh = create_refresh_token(
            user_id=UserId(user.id),
            session_id=session_id,
            refresh_jti=refresh_jti,
            token_version=user.token_version,
        )
        return AuthSession(jwt_access=access, jwt_refresh=refresh)


@dataclass(frozen=True, slots=True)
class GoogleTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    email: str
    google_id: GoogleUserId
    display_name: str | None
    avatar_url: str | None
