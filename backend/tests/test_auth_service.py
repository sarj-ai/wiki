from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import json
from typing import cast, override
import uuid

import httpx
from pydantic import JsonValue
import pytest
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
from app.auth.service import (
    GOOGLE_REVOKE_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    GoogleAuthService,
)
from app.cache.service import CacheNamespace, CacheService
from app.identifiers import GoogleUserId
from app.models.user import User


class _Result:
    def __init__(self, row: User | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> User | None:
        return self._row


class _Session:
    def __init__(self, *rows: User | None, commit_error: Exception | None = None) -> None:
        self._rows = iter(rows)
        self._commit_error = commit_error
        self.added: list[User] = []
        self.commits = 0
        self.refreshed: list[User] = []

    async def execute(self, statement: object) -> _Result:
        del statement
        return _Result(next(self._rows, None))

    def add(self, obj: User) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        if self._commit_error is not None:
            raise self._commit_error
        self.commits += 1

    async def refresh(self, obj: User) -> None:
        # Simulate the DB-side column defaults a real refresh would populate;
        # attributes SQLAlchemy hasn't materialized yet are absent from __dict__.
        if "id" not in obj.__dict__:
            obj.id = uuid.uuid4()
        if "token_version" not in obj.__dict__:
            obj.token_version = 0
        self.refreshed.append(obj)


class _RecordingCache(CacheService):
    def __init__(self) -> None:
        self.deleted: list[tuple[CacheNamespace, str]] = []

    @override
    async def get(self, namespace: CacheNamespace, key: str) -> JsonValue | None:
        raise NotImplementedError

    @override
    async def get_many(self, namespace: CacheNamespace, keys: list[str]) -> dict[str, JsonValue]:
        raise NotImplementedError

    @override
    async def put(
        self, namespace: CacheNamespace, key: str, value: JsonValue, *, ttl: timedelta
    ) -> None:
        raise NotImplementedError

    @override
    async def put_many(
        self, namespace: CacheNamespace, values: Mapping[str, JsonValue], *, ttl: timedelta
    ) -> None:
        raise NotImplementedError

    @override
    async def delete(self, namespace: CacheNamespace, key: str) -> None:
        self.deleted.append((namespace, key))


class _Google:
    """Routes Google's OAuth endpoints through an httpx.MockTransport."""

    def __init__(
        self,
        *,
        token_status: int = 200,
        userinfo_status: int = 200,
        email: str = "user@sarj.ai",
        refresh_token: str | None = "google-refresh",  # ruff: ignore[hardcoded-password-default] — dummy OAuth fixture value
    ) -> None:
        self._token_status = token_status
        self._userinfo_status = userinfo_status
        self._email = email
        self._refresh_token = refresh_token
        self.requests: list[httpx.Request] = []

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == GOOGLE_TOKEN_URL:
            body = {"access_token": "google-access"}
            if self._refresh_token is not None:
                body["refresh_token"] = self._refresh_token
            return httpx.Response(self._token_status, json=body)
        if str(request.url) == GOOGLE_USERINFO_URL:
            return httpx.Response(
                self._userinfo_status,
                json={"id": "gid-1", "email": self._email, "name": "Test User"},
            )
        if str(request.url) == GOOGLE_REVOKE_URL:
            return httpx.Response(200, json={})
        return httpx.Response(404)


def _user(*, token_version: int = 0, refresh_token: str | None = "google-refresh") -> User:  # ruff: ignore[hardcoded-password-default] — dummy OAuth fixture value
    user = User()
    user.id = uuid.uuid4()
    user.email = "user@sarj.ai"
    user.google_id = GoogleUserId("gid-1")
    user.display_name = "Test User"
    user.avatar_url = None
    user.encrypted_access_token = encrypt_text("old-access")
    user.encrypted_refresh_token = (
        encrypt_text(refresh_token) if refresh_token is not None else None
    )
    user.token_version = token_version
    return user


def _service(
    session: _Session,
    google: _Google | None = None,
    cache: CacheService | None = None,
) -> GoogleAuthService:
    return GoogleAuthService(
        db=cast("AsyncSession", session),
        http_client=(google or _Google()).client(),
        cache=cache,
    )


async def test_exchange_rejects_missing_expected_state() -> None:
    with pytest.raises(AuthFailedError):
        await _service(_Session()).exchange_code_for_session("code", "state", None)


async def test_exchange_rejects_mismatched_state() -> None:
    with pytest.raises(AuthFailedError):
        await _service(_Session()).exchange_code_for_session("code", "state-a", "state-b")


async def test_exchange_fails_when_google_token_endpoint_errors() -> None:
    google = _Google(token_status=502)
    with pytest.raises(AuthFailedError):
        await _service(_Session(), google).exchange_code_for_session("code", "s", "s")


async def test_exchange_fails_when_userinfo_errors() -> None:
    google = _Google(userinfo_status=401)
    with pytest.raises(AuthFailedError):
        await _service(_Session(), google).exchange_code_for_session("code", "s", "s")


@pytest.mark.parametrize("email", ["user@evil.example", "no-at-sign"], ids=["wrong", "malformed"])
async def test_exchange_enforces_allowed_domain(email: str) -> None:
    google = _Google(email="attacker@evil.example" if email == "no-at-sign" else email)
    with pytest.raises(DomainRestrictedError):
        await _service(_Session(), google).exchange_code_for_session("code", "s", "s")


async def test_exchange_creates_new_user_and_issues_both_tokens() -> None:
    session = _Session(None)  # no existing user found

    auth = await _service(session, _Google()).exchange_code_for_session("code", "s", "s")

    assert len(session.added) == 1
    created = session.added[0]
    assert created.email == "user@sarj.ai"
    assert created.encrypted_refresh_token is not None
    assert decrypt_text(created.encrypted_refresh_token) == "google-refresh"
    assert decode_token(auth.jwt_access).type == "access"
    assert decode_token(auth.jwt_refresh).type == "refresh"


async def test_exchange_lowercases_email_before_domain_check() -> None:
    session = _Session(None)
    google = _Google(email="Mixed.Case@SARJ.AI")

    await _service(session, google).exchange_code_for_session("code", "s", "s")

    assert session.added[0].email == "mixed.case@sarj.ai"


async def test_relogin_without_google_refresh_token_preserves_stored_one() -> None:
    existing = _user(refresh_token="original-refresh")  # ruff: ignore[hardcoded-password-func-arg] — dummy OAuth fixture value
    session = _Session(existing)
    google = _Google(refresh_token=None)  # Google omits refresh_token on re-consent

    await _service(session, google).exchange_code_for_session("code", "s", "s")

    assert session.added == []
    assert existing.encrypted_refresh_token is not None
    assert decrypt_text(existing.encrypted_refresh_token) == "original-refresh"
    assert decrypt_text(cast("str", existing.encrypted_access_token)) == "google-access"


async def test_relogin_with_google_refresh_token_replaces_stored_one() -> None:
    existing = _user(refresh_token="original-refresh")  # ruff: ignore[hardcoded-password-func-arg] — dummy OAuth fixture value
    session = _Session(existing)

    await _service(session, _Google()).exchange_code_for_session("code", "s", "s")

    assert existing.encrypted_refresh_token is not None
    assert decrypt_text(existing.encrypted_refresh_token) == "google-refresh"


async def test_exchange_wraps_db_failure_in_auth_error() -> None:
    session = _Session(None, commit_error=RuntimeError("db down"))

    with pytest.raises(AuthError):
        await _service(session).exchange_code_for_session("code", "s", "s")


async def test_refresh_rejects_access_token() -> None:
    user = _user()
    token = create_access_token(user_id=user.id, email=user.email, token_version=0)

    with pytest.raises(TokenRevokedError):
        await _service(_Session(user)).refresh_session(token)


async def test_refresh_rejects_unknown_user() -> None:
    token = create_refresh_token(user_id=uuid.uuid4(), token_version=0)

    with pytest.raises(TokenRevokedError):
        await _service(_Session(None)).refresh_session(token)


async def test_refresh_rejects_stale_token_version() -> None:
    user = _user(token_version=2)
    token = create_refresh_token(user_id=user.id, token_version=1)

    with pytest.raises(TokenRevokedError):
        await _service(_Session(user)).refresh_session(token)


async def test_refresh_issues_new_session() -> None:
    user = _user(token_version=3)
    token = create_refresh_token(user_id=user.id, token_version=3)

    auth = await _service(_Session(user)).refresh_session(token)

    assert decode_token(auth.jwt_access).tv == 3
    assert decode_token(auth.jwt_refresh).type == "refresh"


async def test_revoke_with_no_token_is_a_no_op() -> None:
    session = _Session()
    cache = _RecordingCache()

    await _service(session, cache=cache).revoke_session(None)

    assert session.commits == 0
    assert cache.deleted == []


async def test_revoke_ignores_refresh_token() -> None:
    user = _user()
    token = create_refresh_token(user_id=user.id, token_version=0)
    session = _Session(user)

    await _service(session).revoke_session(token)

    assert session.commits == 0


async def test_revoke_bumps_version_revokes_google_and_clears_cache() -> None:
    user = _user()
    token = create_access_token(user_id=user.id, email=user.email, token_version=0)
    session = _Session(user)
    google = _Google()
    cache = _RecordingCache()

    await _service(session, google, cache).revoke_session(token)

    assert user.token_version == 1
    assert session.commits == 1
    revokes = [r for r in google.requests if str(r.url) == GOOGLE_REVOKE_URL]
    assert len(revokes) == 1
    assert cache.deleted == [(CacheNamespace.DRIVE_LIST, str(user.id))]


async def test_revoke_skips_google_when_no_refresh_token_stored() -> None:
    user = _user(refresh_token=None)
    token = create_access_token(user_id=user.id, email=user.email, token_version=0)
    session = _Session(user)
    google = _Google()

    await _service(session, google).revoke_session(token)

    assert user.token_version == 1
    assert google.requests == []


async def test_revoke_survives_undecryptable_refresh_token() -> None:
    user = _user()
    user.encrypted_refresh_token = "not-a-fernet-token"  # ruff: ignore[hardcoded-password-string] — deliberately invalid ciphertext
    token = create_access_token(user_id=user.id, email=user.email, token_version=0)
    session = _Session(user)

    await _service(session).revoke_session(token)

    assert user.token_version == 1
    assert session.commits == 1


async def test_revoke_survives_db_failure_and_still_clears_cache() -> None:
    user = _user()
    token = create_access_token(user_id=user.id, email=user.email, token_version=0)
    session = _Session(user, commit_error=RuntimeError("db down"))
    cache = _RecordingCache()

    await _service(session, cache=cache).revoke_session(token)

    assert cache.deleted == [(CacheNamespace.DRIVE_LIST, str(user.id))]


async def test_resolve_rejects_refresh_token() -> None:
    user = _user()
    token = create_refresh_token(user_id=user.id, token_version=0)

    with pytest.raises(InvalidTokenError):
        await _service(_Session(user)).resolve_current_user(token)


async def test_resolve_rejects_unknown_user() -> None:
    token = create_access_token(user_id=uuid.uuid4(), email="user@sarj.ai", token_version=0)

    with pytest.raises(TokenRevokedError):
        await _service(_Session(None)).resolve_current_user(token)


async def test_resolve_rejects_stale_token_version() -> None:
    user = _user(token_version=5)
    token = create_access_token(user_id=user.id, email=user.email, token_version=4)

    with pytest.raises(TokenRevokedError):
        await _service(_Session(user)).resolve_current_user(token)


async def test_resolve_returns_user_for_valid_token() -> None:
    user = _user(token_version=5)
    token = create_access_token(user_id=user.id, email=user.email, token_version=5)

    resolved = await _service(_Session(user)).resolve_current_user(token)

    assert resolved is user


async def test_exchange_sends_authorization_code_grant() -> None:
    session = _Session(None)
    google = _Google()

    await _service(session, google).exchange_code_for_session("the-code", "s", "s")

    token_request = next(r for r in google.requests if str(r.url) == GOOGLE_TOKEN_URL)
    form = dict(
        pair.split("=", 1) for pair in token_request.content.decode().split("&") if "=" in pair
    )
    assert form["code"] == "the-code"
    assert form["grant_type"] == "authorization_code"


def test_token_payload_round_trip_via_json() -> None:
    user = _user()
    token = create_access_token(user_id=user.id, email=user.email, token_version=7)
    payload = decode_token(token)
    assert json.loads(payload.model_dump_json())["tv"] == 7
