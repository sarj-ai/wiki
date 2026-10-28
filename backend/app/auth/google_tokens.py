from __future__ import annotations

import asyncio
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
import threading
from typing import TYPE_CHECKING, NamedTuple, Protocol, TypeIs, override
import uuid

import httpx
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.elements import ColumnElement

from app.auth.encryption import decrypt_text, encrypt_text
from app.auth.schemas import GoogleTokenResponse
from app.auth.service import GOOGLE_OAUTH_URL, GOOGLE_REVOKE_URL
from app.config import settings
from app.identifiers import UserId
from app.models.user import User
from app.performance import increment, timed


if TYPE_CHECKING:
    from collections.abc import Sequence


_EXPIRY_SKEW = timedelta(minutes=5)
_LEASE_DURATION = timedelta(seconds=10)
_WAITER_TIMEOUT = timedelta(seconds=6)
_REFRESH_HANDLER_TIMEOUT = timedelta(seconds=12)
_HTTP_OK = 200
_PROVIDER_WORK_TASK: ContextVar[asyncio.Task[object] | None] = ContextVar(
    "google_provider_work_task", default=None
)


@contextmanager
def provider_refresh_scope() -> Generator[None]:
    task = asyncio.current_task()
    if task is None:
        msg = "Provider refresh ownership requires an asyncio task"
        raise RuntimeError(msg)
    token = _PROVIDER_WORK_TASK.set(task)
    try:
        yield
    finally:
        _PROVIDER_WORK_TASK.reset(token)


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
    access_ciphertext: str | None = None


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


class GoogleTokenProvider(Protocol):
    async def prepare(
        self,
        user: User,
        *,
        refresh_timeout: timedelta = _REFRESH_HANDLER_TIMEOUT,
    ) -> PreparedGoogleCredentials: ...

    async def ensure_valid(
        self,
        user: User,
        *,
        force_generation: int | None = None,
    ) -> ManagedGoogleToken: ...


class GoogleTokenManager(GoogleTokenProvider):
    def __init__(self, db: AsyncSession, http: httpx.AsyncClient) -> None:
        self._db = db
        self._http = http
        self._lock = asyncio.Lock()

    @override
    async def prepare(
        self,
        user: User,
        *,
        refresh_timeout: timedelta = _REFRESH_HANDLER_TIMEOUT,
    ) -> PreparedGoogleCredentials:
        token = await self.ensure_valid(user)
        loop = asyncio.get_running_loop()
        owner_task = asyncio.current_task()
        user_id = user.id
        latest_token = token
        snapshot_lock = threading.Lock()

        def refresh_handler(
            _request: object,
            *,
            scopes: Sequence[str] | None = None,
        ) -> GoogleRefreshResult:
            del scopes
            nonlocal latest_token
            with snapshot_lock:
                force_generation = latest_token.generation
            future = asyncio.run_coroutine_threadsafe(
                self._refresh_for_provider(
                    user,
                    user_id=user_id,
                    force_generation=force_generation,
                    owner_task=_PROVIDER_WORK_TASK.get() or owner_task,
                ),
                loop,
            )
            try:
                refreshed = future.result(timeout=refresh_timeout.total_seconds())
            except BaseException:
                future.cancel()
                raise
            with snapshot_lock:
                if refreshed.generation >= latest_token.generation:
                    latest_token = refreshed
                selected = latest_token
            # google-auth still requires a timezone-naive UTC expiry even
            # though persistence and application logic use aware datetimes.
            return GoogleRefreshResult(
                selected.token,
                selected.expires_at.astimezone(UTC).replace(tzinfo=None),
            )

        return PreparedGoogleCredentials(user=user, refresh_handler=refresh_handler)

    async def _refresh_for_provider(
        self,
        user: User,
        *,
        user_id: UserId,
        force_generation: int,
        owner_task: asyncio.Task[object] | None,
    ) -> ManagedGoogleToken:
        refresh_task = asyncio.current_task()
        if refresh_task is None:
            msg = "Google credential refresh requires an asyncio task"
            raise RuntimeError(msg)

        def owner_finished(_task: asyncio.Task[object]) -> None:
            refresh_task.cancel()

        if owner_task is not None:
            if owner_task.done():
                raise asyncio.CancelledError
            owner_task.add_done_callback(owner_finished)
        try:
            sessions = self._provider_sessions()
            async with sessions() as db:
                # The fresh credential snapshot checks existence and every CAS
                # field. An identity-only object avoids a redundant full read;
                # it is never added to this independent session.
                snapshot_user = User(id=user_id)
                token = await GoogleTokenManager(db, self._http).ensure_valid(
                    snapshot_user,
                    force_generation=force_generation,
                )
            # Credential commits belong to the independent session. Mirror
            # their persisted snapshot without making caller state dirty.
            self._apply_to_user(user, token)
            return token
        finally:
            if owner_task is not None:
                owner_task.remove_done_callback(owner_finished)

    def _provider_sessions(self) -> async_sessionmaker[AsyncSession]:
        bind = self._db.bind
        if bind is None:
            message = "Google token provider sessions require a database bind"
            raise RuntimeError(message)
        # A caller may bind its session to an existing transaction. Use its
        # engine, never that connection, for the credential lease and CAS.
        engine = bind if isinstance(bind, AsyncEngine) else bind.engine
        return async_sessionmaker(engine, expire_on_commit=False)

    @override
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

            owner = uuid.uuid7()
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
        if snapshot.lease_until is not None and snapshot.lease_until > now:
            # A freshly observed live lease cannot satisfy the UPDATE below.
            # Release the read checkout while retaining the existing retry/CAS
            # path for absent or expired leases.
            await self._db.commit()
            return False
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
                    GOOGLE_OAUTH_URL,
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
            payload = GoogleTokenResponse.model_validate_json(response.content)
        except (ValidationError, ValueError) as exc:
            await self._clear_lease(user, snapshot, owner)
            raise GoogleTokenUnavailableError from exc

        encrypted_refresh = (
            encrypt_text(payload.refresh_token)
            if payload.refresh_token is not None
            else snapshot.credential_ciphertext
        )
        expires_at = received_at + payload.expires_in
        access_ciphertext = encrypt_text(payload.access_token)
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
                encrypted_access_token=access_ciphertext,
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
            access_ciphertext=access_ciphertext,
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
            access_ciphertext=snapshot.access_ciphertext,
        )

    @staticmethod
    def _apply_to_user(user: User, token: ManagedGoogleToken) -> None:
        # Reuse the fresh database ciphertext. Re-encrypting an unchanged token
        # would make the ORM issue an UPDATE on every authenticated Drive read.
        values = (
            ("encrypted_access_token", token.access_ciphertext or encrypt_text(token.token)),
            ("google_access_token_expires_at", token.expires_at),
            ("google_token_generation", token.generation),
        )
        state = inspect(user)
        generation_state = state.attrs.google_token_generation
        current_generation = generation_state.loaded_value
        if (
            not generation_state.history.has_changes()
            and isinstance(current_generation, int)
            and current_generation > token.generation
        ):
            # Independent callbacks can finish closing their sessions in a
            # different order. An older snapshot must not replace a newer one.
            return
        for name, value in values:
            if state.transient:
                setattr(user, name, value)
            elif not state.attrs[name].history.has_changes():
                # The token is already persisted by the lease/CAS flow. A
                # later caller commit must not overwrite a newer generation
                # or discard credential writes the caller already owns.
                set_committed_value(user, name, value)


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(max(int(raw), 1), 30)
    except ValueError:
        return None


def _as_predicate(value: object) -> ColumnElement[bool]:
    if not _is_predicate(value):
        msg = "SQLAlchemy comparison did not produce a predicate"
        raise TypeError(msg)
    return value


def _is_predicate(value: object) -> TypeIs[ColumnElement[bool]]:
    return isinstance(value, ColumnElement)
