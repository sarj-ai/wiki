from __future__ import annotations

# Test-only opaque token fixtures exercise lifecycle behavior, not credentials.
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast, override
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.encryption import encrypt_text
from app.auth.google_tokens import (
    GoogleReauthenticationRequiredError,
    GoogleTokenManager,
    ManagedGoogleToken,
    TokenSnapshot,
)
from app.identifiers import GoogleUserId, UserId
from app.models.user import User


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
            cast("AsyncSession", None),
            cast("httpx.AsyncClient", None),
        )
        self.state = state

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
    user.id = UserId(uuid.uuid4())
    user.token_version = 0
    user.google_token_generation = 0
    return user


async def test_valid_access_token_does_not_refresh() -> None:
    access_token = uuid.uuid4().hex
    state = _SharedTokenState(
        access_token=access_token,
        refresh_token=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    token = await _MemoryTokenManager(state).ensure_valid(_user())

    assert token.token == access_token
    assert state.refreshes == 0


async def test_expiring_access_token_refreshes_proactively() -> None:
    state = _SharedTokenState(
        access_token=uuid.uuid4().hex,
        refresh_token=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(minutes=4),
    )

    token = await _MemoryTokenManager(state).ensure_valid(_user())

    assert token.token == state.access_token
    assert state.refreshes == 1


async def test_concurrent_instances_share_one_refresh_lease() -> None:
    state = _SharedTokenState(
        access_token=uuid.uuid4().hex,
        refresh_token=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    managers = [_MemoryTokenManager(state) for _ in range(12)]

    tokens = await asyncio.gather(*(manager.ensure_valid(_user()) for manager in managers))

    assert {token.token for token in tokens} == {state.access_token}
    assert state.refreshes == 1


async def test_google_auth_callback_forces_one_persisted_refresh() -> None:
    state = _SharedTokenState(
        access_token=uuid.uuid4().hex,
        refresh_token=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    manager = _MemoryTokenManager(state)
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
