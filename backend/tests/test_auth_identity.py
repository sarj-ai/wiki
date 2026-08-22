from __future__ import annotations

from typing import cast, override
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.errors import AuthFailedError
from app.auth.service import GoogleAuthService, GoogleIdentity, GoogleTokens
from app.identifiers import GoogleUserId, UserId
from app.models.user import User


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[User] = []
        self.commits = 0
        self.refreshed: list[User] = []

    def add(self, user: User) -> None:
        self.added.append(user)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, user: User) -> None:
        self.refreshed.append(user)


class _IdentityAuthService(GoogleAuthService):
    def __init__(
        self,
        db: _FakeDb,
        *,
        subject_user: User | None,
        email_user: User | None,
    ) -> None:
        super().__init__(
            db=cast("AsyncSession", db),
            http_client=cast("httpx.AsyncClient", None),
        )
        self.subject_user = subject_user
        self.email_user = email_user

    @override
    async def _find_user_by_google_id(self, google_id: GoogleUserId) -> User | None:
        return self.subject_user

    @override
    async def _find_user_by_email(self, email: str) -> User | None:
        return self.email_user


def _user(*, email: str, google_id: str) -> User:
    user = User(
        email=email,
        google_id=GoogleUserId(google_id),
        display_name="Old name",
        avatar_url="https://example.com/old.png",
        encrypted_access_token=uuid.uuid4().hex,
        encrypted_refresh_token=uuid.uuid4().hex,
    )
    user.id = UserId(uuid.uuid4())
    user.token_version = 0
    return user


def _userinfo(*, email: str, google_id: str) -> GoogleIdentity:
    return GoogleIdentity(
        email=email,
        google_id=GoogleUserId(google_id),
        display_name="New name",
        avatar_url="https://example.com/new.png",
    )


async def test_recycled_email_cannot_take_over_existing_account() -> None:
    existing = _user(email="recycled@sarj.ai", google_id="original-subject")
    original_access_token = existing.encrypted_access_token
    original_refresh_token = existing.encrypted_refresh_token
    db = _FakeDb()
    service = _IdentityAuthService(db, subject_user=None, email_user=existing)

    with pytest.raises(AuthFailedError, match="could not be verified"):
        await service._upsert_user(
            _userinfo(email=existing.email, google_id="attacker-subject"),
            GoogleTokens(access_token=uuid.uuid4().hex, refresh_token=uuid.uuid4().hex),
        )

    assert existing.google_id == "original-subject"
    assert existing.encrypted_access_token == original_access_token
    assert existing.encrypted_refresh_token == original_refresh_token
    assert db.added == []
    assert db.commits == 0


async def test_same_subject_can_refresh_email_and_profile() -> None:
    existing = _user(email="old@sarj.ai", google_id="stable-subject")
    original_access_token = existing.encrypted_access_token
    original_refresh_token = existing.encrypted_refresh_token
    db = _FakeDb()
    service = _IdentityAuthService(db, subject_user=existing, email_user=None)

    result = await service._upsert_user(
        _userinfo(email="new@sarj.ai", google_id="stable-subject"),
        GoogleTokens(access_token=uuid.uuid4().hex, refresh_token=None),
    )

    assert result is existing
    assert existing.google_id == "stable-subject"
    assert existing.email == "new@sarj.ai"
    assert existing.display_name == "New name"
    assert existing.avatar_url == "https://example.com/new.png"
    assert existing.encrypted_access_token != original_access_token
    assert existing.encrypted_refresh_token == original_refresh_token
    assert db.commits == 1
    assert db.refreshed == [existing]


@pytest.mark.parametrize("verified_email", [False, None])
async def test_unverified_or_missing_google_email_fails_auth(
    verified_email: bool | None,
) -> None:
    payload: dict[str, object] = {
        "email": "user@sarj.ai",
        "id": "google-subject",
        "name": "User",
    }
    if verified_email is not None:
        payload["verified_email"] = verified_email

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = GoogleAuthService(
            db=cast("AsyncSession", _FakeDb()),
            http_client=client,
        )
        with pytest.raises(AuthFailedError, match="could not be verified"):
            await service._fetch_userinfo("google-access-token")
