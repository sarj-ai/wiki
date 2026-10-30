from __future__ import annotations

# Test-only opaque token fixtures exercise lifecycle behavior, not credentials.
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import secrets
from typing import override
import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import make_transient_to_detached
from sqlalchemy.orm.attributes import set_committed_value

from app.auth.encryption import encrypt_text
from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    GoogleTokenProvider,
    ManagedGoogleToken,
    TokenSnapshot,
)
from app.identifiers import GoogleUserId, UserId
from app.models.user import User
from tests.support import dependency


@dataclass
class _SharedTokenState:
    access_token: str | None
    refresh_token: str | None
    expires_at: datetime | None
    generation: int = 0
    token_version: int = 0
    lease_owner: uuid.UUID | None = None
    refreshes: int = 0

    def __post_init__(self) -> None:
        self.lock = asyncio.Lock()


class _MemoryTokenManager(GoogleTokenManager):
    def __init__(self, state: _SharedTokenState) -> None:
        super().__init__(
            dependency(None),
            dependency(None),
        )
        self.state = state

    @override
    async def _refresh_for_provider(
        self,
        user: User,
        *,
        user_id: uuid.UUID,
        force_generation: int,
        owner_task: asyncio.Task[object] | None,
    ) -> ManagedGoogleToken:
        # The shared in-memory lease replaces independent database sessions.
        return await self.ensure_valid(user, force_generation=force_generation)

    @override
    async def _snapshot(self, user: User) -> TokenSnapshot:
        del user
        async with self.state.lock:
            return TokenSnapshot(
                access_ciphertext=(
                    encrypt_text(self.state.access_token)
                    if self.state.access_token is not None
                    else None
                ),
                credential_ciphertext=(
                    encrypt_text(self.state.refresh_token)
                    if self.state.refresh_token is not None
                    else None
                ),
                expires_at=self.state.expires_at,
                generation=self.state.generation,
                session_revision=self.state.token_version,
                lease_until=(
                    datetime.now(UTC) + timedelta(seconds=10)
                    if self.state.lease_owner is not None
                    else None
                ),
            )

    @override
    async def _acquire_lease(
        self,
        user: User,
        snapshot: TokenSnapshot,
        owner: uuid.UUID,
        now: datetime,
    ) -> bool:
        del user, now
        async with self.state.lock:
            if (
                self.state.lease_owner is not None
                or self.state.generation != snapshot.generation
                or self.state.token_version != snapshot.session_revision
            ):
                return False
            self.state.lease_owner = owner
            return True

    @override
    async def _refresh_owned(
        self,
        user: User,
        snapshot: TokenSnapshot,
        owner: uuid.UUID,
    ) -> ManagedGoogleToken:
        del user
        await asyncio.sleep(0.02)
        async with self.state.lock:
            assert self.state.lease_owner is owner
            assert self.state.generation == snapshot.generation
            self.state.refreshes += 1
            self.state.generation += 1
            self.state.access_token = f"fresh-{self.state.generation}"
            self.state.expires_at = datetime.now(UTC) + timedelta(hours=1)
            self.state.lease_owner = None
            return ManagedGoogleToken(
                token=self.state.access_token,
                expires_at=self.state.expires_at,
                generation=self.state.generation,
            )


def _user() -> User:
    user = User(
        email="user@sarj.ai",
        google_id=GoogleUserId("google-subject"),
        display_name="User",
    )
    user.id = UserId(uuid.uuid7())
    user.token_version = 0
    user.google_token_generation = 0
    return user


async def test_valid_access_token_does_not_refresh() -> None:
    access_token = secrets.token_urlsafe()
    state = _SharedTokenState(
        access_token=access_token,
        refresh_token=secrets.token_urlsafe(),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    manager: GoogleTokenProvider = _MemoryTokenManager(state)
    token = await manager.ensure_valid(_user())

    assert token.token == access_token
    assert state.refreshes == 0


async def test_preparing_valid_credentials_reuses_database_ciphertext() -> None:
    user = _user()
    ciphertext = encrypt_text(secrets.token_urlsafe())
    expiry = datetime.now(UTC) + timedelta(hours=1)
    set_committed_value(user, "encrypted_access_token", ciphertext)
    set_committed_value(user, "google_access_token_expires_at", expiry)
    set_committed_value(user, "google_token_generation", 0)
    reads = 0

    class SnapshotManager(GoogleTokenManager):
        @override
        async def _snapshot(self, user: User) -> TokenSnapshot:
            nonlocal reads
            reads += 1
            return TokenSnapshot(ciphertext, None, expiry, 0, 0, None)

    manager = SnapshotManager(dependency(None), dependency(None))
    await manager.prepare(user)
    await manager.prepare(user)

    state = inspect(user)
    assert state is not None
    assert not state.attrs.encrypted_access_token.history.has_changes()
    assert user.encrypted_access_token == ciphertext
    assert reads == 2


async def test_expiring_access_token_refreshes_proactively() -> None:
    state = _SharedTokenState(
        access_token=secrets.token_urlsafe(),
        refresh_token=secrets.token_urlsafe(),
        expires_at=datetime.now(UTC) + timedelta(minutes=4),
    )

    token = await _MemoryTokenManager(state).ensure_valid(_user())

    assert token.token == state.access_token
    assert state.refreshes == 1


async def test_concurrent_instances_share_one_refresh_lease() -> None:
    state = _SharedTokenState(
        access_token=secrets.token_urlsafe(),
        refresh_token=secrets.token_urlsafe(),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    managers = [_MemoryTokenManager(state) for _ in range(12)]

    tokens = await asyncio.gather(*(manager.ensure_valid(_user()) for manager in managers))

    assert {token.token for token in tokens} == {state.access_token}
    assert state.refreshes == 1


async def test_google_auth_callback_forces_one_persisted_refresh() -> None:
    state = _SharedTokenState(
        access_token=secrets.token_urlsafe(),
        refresh_token=secrets.token_urlsafe(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    manager: GoogleTokenProvider = _MemoryTokenManager(state)
    prepared = await manager.prepare(_user())

    token, expiry = await asyncio.to_thread(prepared.refresh_handler, object())

    assert token == state.access_token
    assert state.expires_at is not None
    assert expiry == state.expires_at.replace(tzinfo=None)
    assert state.refreshes == 1


async def test_missing_refresh_token_requires_reauthentication() -> None:
    state = _SharedTokenState(
        access_token=None,
        refresh_token=None,
        expires_at=None,
    )

    with pytest.raises(GoogleReauthenticationRequiredError):
        await _MemoryTokenManager(state).ensure_valid(_user())


def _persisted_user() -> User:
    user = _user()
    user.encrypted_access_token = encrypt_text("initial-token")
    user.google_access_token_expires_at = datetime.now(UTC) + timedelta(hours=1)
    make_transient_to_detached(user)
    return user


def test_persisted_credential_snapshot_does_not_mark_request_user_dirty() -> None:
    user = _persisted_user()
    ciphertext = encrypt_text("fresh-token")
    expiry = datetime.now(UTC) + timedelta(hours=2)
    GoogleTokenManager._apply_to_user(
        user, ManagedGoogleToken("fresh-token", expiry, 1, ciphertext)
    )

    assert user.encrypted_access_token == ciphertext
    assert user.google_access_token_expires_at == expiry
    assert user.google_token_generation == 1
    state = inspect(user)
    assert state is not None
    assert not state.modified


def test_persisted_credential_snapshot_preserves_caller_pending_writes() -> None:
    user = _persisted_user()
    pending_ciphertext = encrypt_text("caller-token")
    user.encrypted_access_token = pending_ciphertext
    user.google_token_generation = 3
    user.display_name = "Caller-owned change"
    ciphertext = encrypt_text("fresh-token")
    expiry = datetime.now(UTC) + timedelta(hours=2)
    GoogleTokenManager._apply_to_user(
        user, ManagedGoogleToken("fresh-token", expiry, 1, ciphertext)
    )

    assert user.encrypted_access_token == pending_ciphertext
    assert user.google_token_generation == 3
    assert user.display_name == "Caller-owned change"
    assert user.google_access_token_expires_at == expiry
    state = inspect(user)
    assert state is not None
    assert state.attrs.encrypted_access_token.history.has_changes()
    assert state.attrs.google_token_generation.history.has_changes()
    assert not state.attrs.google_access_token_expires_at.history.has_changes()


async def test_refresh_bridge_timeout_cancels_its_pending_coroutine() -> None:
    started = asyncio.Event()
    finished = asyncio.Event()

    class BlockingTokenManager(_MemoryTokenManager):
        @override
        async def _refresh_for_provider(
            self,
            user: User,
            *,
            user_id: uuid.UUID,
            force_generation: int,
            owner_task: asyncio.Task[object] | None,
        ) -> ManagedGoogleToken:
            started.set()
            try:
                await asyncio.Event().wait()
                raise AssertionError
            finally:
                finished.set()

    state = _SharedTokenState(
        access_token=secrets.token_urlsafe(),
        refresh_token=secrets.token_urlsafe(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    prepared = await BlockingTokenManager(state).prepare(
        _user(), refresh_timeout=timedelta(milliseconds=30)
    )
    with pytest.raises(TimeoutError):
        await asyncio.to_thread(prepared.refresh_handler, object())
    assert started.is_set()
    await asyncio.wait_for(finished.wait(), timeout=1)


async def test_out_of_order_provider_callbacks_keep_snapshot_and_generation_monotonic() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    forced_generations: list[int] = []
    expiry = datetime.now(UTC) + timedelta(hours=2)
    old = ManagedGoogleToken("snapshot-1", expiry, 1, encrypt_text("snapshot-1"))
    newest = ManagedGoogleToken("snapshot-2", expiry, 2, encrypt_text("snapshot-2"))

    class OutOfOrderTokenManager(_MemoryTokenManager):
        @override
        async def _refresh_for_provider(
            self,
            user: User,
            *,
            user_id: uuid.UUID,
            force_generation: int,
            owner_task: asyncio.Task[object] | None,
        ) -> ManagedGoogleToken:
            forced_generations.append(force_generation)
            if len(forced_generations) == 1:
                first_started.set()
                await release_first.wait()
                selected = old
            else:
                selected = newest
            self._apply_to_user(user, selected)
            return selected

    user = _persisted_user()
    state = _SharedTokenState(
        access_token=secrets.token_urlsafe(),
        refresh_token=secrets.token_urlsafe(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    prepared = await OutOfOrderTokenManager(state).prepare(user)
    user.display_name = "Caller-owned change"
    first = asyncio.create_task(asyncio.to_thread(prepared.refresh_handler, object()))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = await asyncio.to_thread(prepared.refresh_handler, object())
    assert second.token == newest.token
    release_first.set()
    assert (await first).token == newest.token
    assert user.google_token_generation == 2
    assert user.encrypted_access_token == newest.access_ciphertext
    assert user.google_access_token_expires_at == newest.expires_at
    assert (await asyncio.to_thread(prepared.refresh_handler, object())).token == newest.token
    assert forced_generations == [0, 0, 2]
    mapped = inspect(user)
    assert mapped is not None
    assert mapped.attrs.display_name.history.has_changes()
    assert not mapped.attrs.encrypted_access_token.history.has_changes()
    assert not mapped.attrs.google_token_generation.history.has_changes()
