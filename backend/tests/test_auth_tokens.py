from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import override
import uuid

import httpx
import jwt
from loguru import logger
import pytest

from app.auth.encryption import encrypt_text
from app.auth.errors import InvalidTokenError, TokenRevokedError
from app.auth.jwt import (
    ALGORITHM,
    TOKEN_AUDIENCE,
    TOKEN_ISSUER,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.auth.service import AccessIdentity, GoogleAuthService
from app.config import settings
from app.identifiers import AuthSessionFamilyId, AuthSessionId, TokenId, UserId
from app.models.auth_session import UserSession
from app.models.user import User
from tests.support import dependency


def test_tokens_have_verified_identity_and_no_email_claim() -> None:
    user_id = UserId(uuid.uuid7())
    session_id = AuthSessionId(uuid.uuid7())
    token = create_access_token(user_id, session_id)

    raw = jwt.decode(
        token,
        settings.SECRET_KEY.get_secret_value(),
        algorithms=[ALGORITHM],
        audience=TOKEN_AUDIENCE,
        issuer=TOKEN_ISSUER,
    )
    parsed = decode_token(token)

    assert "email" not in raw
    assert parsed.sub == user_id
    assert parsed.sid == session_id
    assert parsed.type == "access"


@pytest.mark.parametrize(
    ("claim", "value"),
    [("iss", "another-service"), ("aud", "another-api")],
)
def test_tokens_reject_wrong_issuer_or_audience(claim: str, value: str) -> None:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": str(uuid.uuid7()),
        "sid": str(uuid.uuid7()),
        "jti": str(uuid.uuid7()),
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "type": "access",
    }
    payload[claim] = value
    token = jwt.encode(payload, settings.SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)

    with pytest.raises(InvalidTokenError):
        decode_token(token)


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _JoinedResult:
    def __init__(self, row: AccessIdentity | None) -> None:
        self._row = row

    def one_or_none(self) -> AccessIdentity | None:
        return self._row


class _JoinedDb:
    def __init__(self, row: AccessIdentity | None) -> None:
        self.calls = 0
        self._row = row

    async def execute(self, statement: object) -> _JoinedResult:
        del statement
        self.calls += 1
        return _JoinedResult(self._row)


class _SessionService(GoogleAuthService):
    def __init__(
        self, user: User, session: UserSession, http: httpx.AsyncClient | None = None
    ) -> None:
        self.db = _FakeDb()
        super().__init__(
            dependency(self.db),
            http if http is not None else dependency(object()),
        )
        self.user = user
        self.session = session
        self.revoked_families: list[AuthSessionFamilyId] = []

    @override
    async def _load_access_identity(
        self,
        session_id: AuthSessionId,
        user_id: UserId,
    ) -> AccessIdentity | None:
        if self.session.id != session_id or self.user.id != user_id:
            return None
        return AccessIdentity(session=self.session, user=self.user)

    @override
    async def _load_user(self, user_id: UserId) -> User | None:
        return self.user if self.user.id == user_id else None

    @override
    async def _load_session(
        self,
        session_id: AuthSessionId,
        *,
        for_update: bool = False,
    ) -> UserSession | None:
        del for_update
        return self.session if self.session.id == session_id else None

    @override
    async def _revoke_session_family(
        self, family_id: AuthSessionFamilyId, revoked_at: datetime
    ) -> None:
        self.revoked_families.append(family_id)
        self.session.revoked_at = revoked_at


@dataclass(frozen=True, slots=True)
class _AuthFixture:
    user: User
    session: UserSession


def _user_and_session() -> _AuthFixture:
    user_id = UserId(uuid.uuid7())
    session_id = AuthSessionId(uuid.uuid7())
    user = User(
        id=user_id,
        email="person@sarj.ai",
        google_id="google-subject",
        display_name=None,
        avatar_url=None,
        encrypted_access_token=None,
        encrypted_refresh_token=None,
        token_version=0,
    )
    now = datetime.now(UTC)
    session = UserSession(
        id=session_id,
        family_id=AuthSessionFamilyId(session_id),
        user_id=user_id,
        refresh_jti=TokenId(uuid.uuid7()),
        expires_at=now + timedelta(days=7),
        revoked_at=None,
        created_at=now,
        updated_at=now,
    )
    return _AuthFixture(user=user, session=session)


async def test_refresh_rotates_jti_and_replay_revokes_family() -> None:
    fixture = _user_and_session()
    user, session = fixture.user, fixture.session
    service = _SessionService(user, session)
    original = create_refresh_token(UserId(user.id), session.id, session.refresh_jti)

    replacement = await service.refresh_session(original)
    replacement_payload = decode_token(replacement.jwt_refresh)

    assert replacement_payload.jti == session.refresh_jti
    assert replacement_payload.jti != decode_token(original).jti
    assert service.db.commits == 1

    with pytest.raises(TokenRevokedError):
        await service.refresh_session(original)
    assert service.revoked_families == [session.family_id]


async def test_access_token_requires_an_active_matching_session() -> None:
    fixture = _user_and_session()
    user, session = fixture.user, fixture.session
    service = _SessionService(user, session)
    token = create_access_token(UserId(user.id), session.id)

    assert await service.resolve_current_user(token) is user
    session.revoked_at = datetime.now(UTC)
    with pytest.raises(TokenRevokedError):
        await service.resolve_current_user(token)


async def test_access_token_loads_session_and_user_in_one_query() -> None:
    fixture = _user_and_session()
    db = _JoinedDb(AccessIdentity(session=fixture.session, user=fixture.user))
    service = GoogleAuthService(
        dependency(db),
        dependency(object()),
    )
    token = create_access_token(UserId(fixture.user.id), fixture.session.id)

    assert await service.resolve_current_user(token) is fixture.user
    assert db.calls == 1


@pytest.mark.parametrize(
    ("status_code", "failed"), [(200, False), (400, True), (429, True), (503, True), (None, True)]
)
async def test_google_revocation_failures_are_observed_after_local_logout(
    status_code: int | None, failed: bool
) -> None:
    fixture = _user_and_session()
    user, session = fixture.user, fixture.session
    refresh_token = "synthetic-google-refresh"
    user.encrypted_refresh_token = encrypt_text(refresh_token)
    user.google_token_generation = 7
    messages: list[str] = []
    calls: list[tuple[httpx.Request, int]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append((request, service.db.commits))
        if status_code is None:
            msg = "synthetic transport failure"
            raise httpx.ConnectError(msg, request=request)
        return httpx.Response(status_code, text="synthetic provider detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        service = _SessionService(user, session, client)
        handler = logger.add(messages.append, format="{message}", level="WARNING")
        try:
            await service.revoke_session(create_access_token(UserId(user.id), session.id))
        finally:
            logger.remove(handler)

    assert len(calls) == 1
    request, commits_before_provider = calls[0]
    assert request.url == "https://oauth2.googleapis.com/revoke"
    assert request.content == f"token={refresh_token}".encode()
    assert commits_before_provider == 1
    assert session.revoked_at is not None
    assert user.encrypted_refresh_token is None
    assert user.token_version == 1
    assert user.google_token_generation == 8
    warning = "logout: Google token revocation failed"
    assert any(warning in message for message in messages) is failed
    assert all(refresh_token not in message for message in messages)
    assert all("synthetic provider detail" not in message for message in messages)
    assert all("synthetic transport failure" not in message for message in messages)
