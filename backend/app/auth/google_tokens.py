from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from typing import TYPE_CHECKING, NamedTuple, cast
import uuid

import httpx
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.auth.encryption import decrypt_text, encrypt_text
from app.auth.schemas import GoogleTokenResponse
from app.auth.service import GOOGLE_REVOKE_URL, GOOGLE_TOKEN_URL
from app.config import settings
from app.models.user import User
from app.performance import increment, timed


if TYPE_CHECKING:
    from collections.abc import Sequence


_EXPIRY_SKEW = timedelta(minutes=5)
_LEASE_DURATION = timedelta(seconds=10)
_WAITER_TIMEOUT = timedelta(seconds=6)
_REFRESH_HANDLER_TIMEOUT = timedelta(seconds=12)
_HTTP_OK = 200


class GoogleReauthenticationRequiredError(Exception):
    pass


class GoogleTokenUnavailableError(Exception):
    def __init__(self, *, retry_after: int | None = None) -> None:
        super().__init__("Google authorization is temporarily unavailable")
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class ManagedGoogleToken:
    token: str
    expires_at: datetime
    generation: int


@dataclass(frozen=True, slots=True)
class TokenSnapshot:
    access_ciphertext: str | None
    credential_ciphertext: str | None
    expires_at: datetime | None
    generation: int
    session_revision: int
    lease_until: datetime | None


class GoogleRefreshResult(NamedTuple):
    token: str
    expiry: datetime


@dataclass(frozen=True, slots=True)
class PreparedGoogleCredentials:
    user: User
    refresh_handler: Callable[..., GoogleRefreshResult]


class GoogleTokenManager:
    def __init__(self, db: AsyncSession, http: httpx.AsyncClient) -> None:
        self._db = db
        self._http = http
        self._lock = asyncio.Lock()

    async def prepare(self, user: User) -> PreparedGoogleCredentials:
        token = await self.ensure_valid(user)
        self._apply_to_user(user, token)
        loop = asyncio.get_running_loop()
        generation = token.generation

        def refresh_handler(
            _request: object,
            *,
            scopes: Sequence[str] | None = None,
        ) -> GoogleRefreshResult:
            del scopes
            nonlocal generation
            future = asyncio.run_coroutine_threadsafe(
                self.ensure_valid(user, force_generation=generation),
                loop,
            )
            refreshed = future.result(timeout=_REFRESH_HANDLER_TIMEOUT.total_seconds())
            generation = refreshed.generation
            # google-auth still requires a timezone-naive UTC expiry even
            # though persistence and application logic use aware datetimes.
            return GoogleRefreshResult(
                refreshed.token,
                refreshed.expires_at.astimezone(UTC).replace(tzinfo=None),
            )

        return PreparedGoogleCredentials(user=user, refresh_handler=refresh_handler)

    async def ensure_valid(
        self,
        user: User,
        *,
        force_generation: int | None = None,
    ) -> ManagedGoogleToken:
        async with self._lock:
            token = await self._ensure_valid_locked(
                user,
                force_generation=force_generation,
            )
            # SQLAlchemy request objects belong to the event-loop thread. Keep
            # them current here so the synchronous google-auth refresh bridge
            # never mutates ORM state from a Drive worker thread.
            self._apply_to_user(user, token)
            return token

    async def _ensure_valid_locked(
        self,
        user: User,
        *,
        force_generation: int | None,
    ) -> ManagedGoogleToken:
        deadline = asyncio.get_running_loop().time() + _WAITER_TIMEOUT.total_seconds()
        while True:
            snapshot = await self._snapshot(user)
            now = datetime.now(UTC)
            forced_current_generation = (
                force_generation is not None and snapshot.generation == force_generation
            )
            if not forced_current_generation and self._is_valid(snapshot, now):
                return self._managed(snapshot)
            if snapshot.credential_ciphertext is None:
                raise GoogleReauthenticationRequiredError

            owner = uuid.uuid4()
            acquired = await self._acquire_lease(user, snapshot, owner, now)
            if acquired:
                return await self._refresh_owned(user, snapshot, owner)

            if asyncio.get_running_loop().time() >= deadline:
                raise GoogleTokenUnavailableError(retry_after=1)
            await asyncio.sleep(0.1 + secrets.randbelow(151) / 1000)

    async def _snapshot(self, user: User) -> TokenSnapshot:
        row = (
            await self._db.execute(
                select(
                    User.encrypted_access_token,
                    User.encrypted_refresh_token,
                    User.google_access_token_expires_at,
                    User.google_token_generation,
                    User.token_version,
                    User.google_refresh_lease_until,
                ).where(User.id == user.id)
            )
        ).one_or_none()
        if row is None:
            raise GoogleReauthenticationRequiredError
        return TokenSnapshot(
            access_ciphertext=row[0],
            credential_ciphertext=row[1],
            expires_at=row[2],
            generation=row[3],
            session_revision=row[4],
            lease_until=row[5],
        )

    async def _acquire_lease(
        self,
        user: User,
        snapshot: TokenSnapshot,
        owner: uuid.UUID,
        now: datetime,
    ) -> bool:
        session_revision_column = User.token_version
        credential_generation_column = User.google_token_generation
        credential_ciphertext_column = User.encrypted_refresh_token
        result = await self._db.execute(
            update(User)
            .where(
                User.id == user.id,
                session_revision_column == snapshot.session_revision,
                _as_predicate(
                    credential_generation_column.isnot_distinct_from(snapshot.generation)
                ),
                _as_predicate(
                    credential_ciphertext_column.isnot_distinct_from(snapshot.credential_ciphertext)
                ),
                or_(
                    User.google_refresh_lease_until.is_(None),
                    User.google_refresh_lease_until <= now,
                ),
            )
            .values(
                google_refresh_lease_owner=owner,
                google_refresh_lease_until=now + _LEASE_DURATION,
            )
            .returning(User.id)
        )
        acquired = result.scalar_one_or_none() is not None
        await self._db.commit()
        return acquired

    async def _refresh_owned(
        self,
        user: User,
        snapshot: TokenSnapshot,
        owner: uuid.UUID,
    ) -> ManagedGoogleToken:
        increment("google_token_refresh_count")
        refresh_token = decrypt_text(snapshot.credential_ciphertext or "")
        try:
            with timed("google_token_refresh"):
                response = await self._http.post(
                    GOOGLE_TOKEN_URL,
                    data={
                        "client_id": settings.GOOGLE_CLIENT_ID,
                        "client_secret": settings.GOOGLE_CLIENT_SECRET.get_secret_value(),
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    timeout=5.0,
                )
        except asyncio.CancelledError:
            await self._clear_lease(user, snapshot, owner)
            raise
        except httpx.HTTPError as exc:
            await self._clear_lease(user, snapshot, owner)
            raise GoogleTokenUnavailableError from exc
        try:
            return await self._persist_refresh_response(
                user,
                snapshot,
                owner,
                response,
                received_at=datetime.now(UTC),
            )
        except asyncio.CancelledError:
            await self._clear_lease(user, snapshot, owner)
            raise

    async def _persist_refresh_response(
        self,
        user: User,
        snapshot: TokenSnapshot,
        owner: uuid.UUID,
        response: httpx.Response,
        *,
        received_at: datetime,
    ) -> ManagedGoogleToken:
        if response.status_code != _HTTP_OK:
            await self._handle_refresh_error(user, snapshot, owner, response)
        try:
            payload = GoogleTokenResponse.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            await self._clear_lease(user, snapshot, owner)
            raise GoogleTokenUnavailableError from exc

        encrypted_refresh = (
            encrypt_text(payload.refresh_token)
            if payload.refresh_token is not None
            else snapshot.credential_ciphertext
        )
        expires_at = received_at + payload.expires_in
        session_revision_column = User.token_version
        credential_generation_column = User.google_token_generation
        credential_ciphertext_column = User.encrypted_refresh_token
        lease_owner_column = User.google_refresh_lease_owner
        result = await self._db.execute(
            update(User)
            .where(
                User.id == user.id,
                session_revision_column == snapshot.session_revision,
                _as_predicate(
                    credential_generation_column.isnot_distinct_from(snapshot.generation)
                ),
                lease_owner_column == owner,
                _as_predicate(
                    credential_ciphertext_column.isnot_distinct_from(snapshot.credential_ciphertext)
                ),
            )
            .values(
                encrypted_access_token=encrypt_text(payload.access_token),
                encrypted_refresh_token=encrypted_refresh,
                google_access_token_expires_at=expires_at,
                google_token_generation=snapshot.generation + 1,
                google_refresh_lease_owner=None,
                google_refresh_lease_until=None,
            )
            .returning(User.id)
        )
        stored = result.scalar_one_or_none() is not None
        await self._db.commit()
        if not stored:
            if payload.refresh_token is not None:
                await self._best_effort_revoke(payload.refresh_token)
            raise GoogleReauthenticationRequiredError
        logger.info(
            "google token refreshed generation={generation}",
            generation=snapshot.generation + 1,
        )
        return ManagedGoogleToken(
            token=payload.access_token,
            expires_at=expires_at,
            generation=snapshot.generation + 1,
        )

    async def _handle_refresh_error(
        self,
        user: User,
        snapshot: TokenSnapshot,
        owner: uuid.UUID,
        response: httpx.Response,
    ) -> None:
        reason: str | None = None
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                reason = body["error"]
        except ValueError:
            pass
        if reason in {"invalid_grant", "invalid_client"}:
            session_revision_column = User.token_version
            credential_generation_column = User.google_token_generation
            lease_owner_column = User.google_refresh_lease_owner
            await self._db.execute(
                update(User)
                .where(
                    User.id == user.id,
                    session_revision_column == snapshot.session_revision,
                    _as_predicate(
                        credential_generation_column.isnot_distinct_from(snapshot.generation)
                    ),
                    lease_owner_column == owner,
                )
                .values(
                    encrypted_access_token=None,
                    encrypted_refresh_token=None,
                    google_access_token_expires_at=None,
                    google_token_generation=snapshot.generation + 1,
                    google_refresh_lease_owner=None,
                    google_refresh_lease_until=None,
                    token_version=snapshot.session_revision + 1,
                )
            )
            await self._db.commit()
            raise GoogleReauthenticationRequiredError
        retry_after = _retry_after(response)
        await self._clear_lease(user, snapshot, owner)
        raise GoogleTokenUnavailableError(retry_after=retry_after)

    async def _clear_lease(
        self,
        user: User,
        snapshot: TokenSnapshot,
        owner: uuid.UUID,
    ) -> None:
        session_revision_column = User.token_version
        credential_generation_column = User.google_token_generation
        lease_owner_column = User.google_refresh_lease_owner
        await self._db.execute(
            update(User)
            .where(
                User.id == user.id,
                session_revision_column == snapshot.session_revision,
                _as_predicate(
                    credential_generation_column.isnot_distinct_from(snapshot.generation)
                ),
                lease_owner_column == owner,
            )
            .values(google_refresh_lease_owner=None, google_refresh_lease_until=None)
        )
        await self._db.commit()

    async def _best_effort_revoke(self, refresh_token: str) -> None:
        try:
            await self._http.post(GOOGLE_REVOKE_URL, data={"token": refresh_token}, timeout=5.0)
        except httpx.HTTPError:
            logger.warning("google orphan token revocation failed")

    @staticmethod
    def _is_valid(snapshot: TokenSnapshot, now: datetime) -> bool:
        return (
            snapshot.access_ciphertext is not None
            and snapshot.expires_at is not None
            and snapshot.expires_at > now + _EXPIRY_SKEW
        )

    @staticmethod
    def _managed(snapshot: TokenSnapshot) -> ManagedGoogleToken:
        if snapshot.access_ciphertext is None or snapshot.expires_at is None:
            raise GoogleReauthenticationRequiredError
        return ManagedGoogleToken(
            token=decrypt_text(snapshot.access_ciphertext),
            expires_at=snapshot.expires_at,
            generation=snapshot.generation,
        )

    @staticmethod
    def _apply_to_user(user: User, token: ManagedGoogleToken) -> None:
        user.encrypted_access_token = encrypt_text(token.token)
        user.google_access_token_expires_at = token.expires_at
        user.google_token_generation = token.generation


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(max(int(raw), 1), 30)
    except ValueError:
        return None


def _as_predicate(value: object) -> ColumnElement[bool]:
    if not isinstance(value, ColumnElement):
        msg = "SQLAlchemy comparison did not produce a predicate"
        raise TypeError(msg)
    return cast("ColumnElement[bool]", value)
