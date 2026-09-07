from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.auth.dependencies import get_current_user
from app.config import settings
from app.csrf import CookieOriginMiddleware
from app.database import get_db
from app.document_transform import qa_router
from app.document_transform.qa_grants import IssuedQaGrant, QaCapability
from app.identifiers import AuthSessionId, GoogleUserId, UserId
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId
from app.models.user import User
from app.tasks.internal_auth import GoogleOidcTaskRequestAuthenticator


_RELEASE = "a" * 40
_DIGEST = "sha256:" + "b" * 64
_AUDIENCE = "https://api.example.test/internal/document-transform-qa/exchange"
_SERVICE_ACCOUNT = "qa@example-project.iam.gserviceaccount.com"
_NONCE = "11111111-1111-4111-8111-111111111111"


class _Db:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def _user() -> User:
    return User(
        id=UserId(uuid.UUID("22222222-2222-4222-8222-222222222222")),
        google_id=GoogleUserId("google-user"),
        email="qa@example.test",
        token_version=3,
    )


def _body(*, operation: str = "consume") -> dict[str, str]:
    return {
        "credential": "c" * 43,
        "execution_name": "job/executions/one",
        "request_nonce": _NONCE,
        "release_sha": _RELEASE,
        "image_digest": _DIGEST,
        "source_folder_id": "source",
        "destination_folder_id": "destination",
        "mode": "both",
        "operation": operation,
    }


def _issuer_body() -> dict[str, str]:
    excluded = {"credential", "execution_name", "request_nonce", "operation"}
    return {key: value for key, value in _body().items() if key not in excluded}


def _application(db: _Db, user: User) -> FastAPI:
    application = FastAPI()
    application.add_middleware(CookieOriginMiddleware, allowed_origin=settings.frontend_origin)
    application.include_router(qa_router.issuer_router)
    application.include_router(qa_router.workload_router)
    application.dependency_overrides[get_current_user] = lambda: user
    application.dependency_overrides[get_db] = lambda: db
    return application


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RELEASE_SHA", _RELEASE)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST", _DIGEST)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_AUDIENCE", _AUDIENCE)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT", _SERVICE_ACCOUNT)


def _oidc(monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]) -> None:
    def verifier(_token: str, _audience: str) -> dict[str, Any]:
        return claims

    def authenticator(**_kwargs: object) -> GoogleOidcTaskRequestAuthenticator:
        return GoogleOidcTaskRequestAuthenticator(
            audience=_AUDIENCE,
            service_account_email=_SERVICE_ACCOUNT,
            verifier=verifier,
        )

    monkeypatch.setattr(
        qa_router,
        "GoogleOidcTaskRequestAuthenticator",
        authenticator,
    )


@pytest.mark.parametrize("origin", [None, "https://hostile.test", "null"])
async def test_issuer_rejects_missing_hostile_and_null_origin(
    monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    _configure(monkeypatch)
    db = _Db()
    application = _application(db, _user())
    headers = {} if origin is None else {"Origin": origin}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/grants",
            json=_issuer_body(),
            headers=headers,
            cookies={"access_token": "cookie"},
        )
    assert response.status_code == 403
    assert db.commits == 0


async def test_issuer_rejects_bearer_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/grants",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin, "Authorization": "Bearer token"},
        )
    assert response.status_code == 401
    assert db.commits == 0


async def test_issuer_binds_exact_cookie_user_session_and_disables_caching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    user = _user()
    session_id = AuthSessionId(uuid.UUID("33333333-3333-4333-8333-333333333333"))
    monkeypatch.setattr(
        qa_router,
        "decode_token",
        lambda _token: SimpleNamespace(type="access", sub=user.id, sid=session_id, tv=3),
    )
    captured: dict[str, object] = {}

    async def issue(_db: object, **kwargs: object) -> IssuedQaGrant:
        captured.update(kwargs)
        return IssuedQaGrant(
            grant_id=DocumentTransformQaGrantId(uuid.UUID("44444444-4444-4444-8444-444444444444")),
            token="grant-token",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    monkeypatch.setattr(qa_router, "issue_qa_grant", issue)
    db = _Db()
    application = _application(db, user)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/grants",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin},
            cookies={"access_token": "cookie"},
        )
    assert response.status_code == 201
    assert captured["issuer_user_id"] == captured["user_id"] == user.id
    assert captured["session_id"] == session_id
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


async def test_default_off_exchange_is_503_before_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_AUDIENCE", None)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT", None)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post("/internal/document-transform-qa/exchange", json=_body())
    assert response.status_code == 503
    assert db.commits == db.rollbacks == 0


@pytest.mark.parametrize(
    "claims",
    [
        {
            "iss": "evil.example",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
        {
            "iss": "https://accounts.google.com",
            "aud": "wrong",
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": "other@example.test",
            "email_verified": True,
        },
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": False,
        },
    ],
    ids=("bad-issuer", "bad-audience", "bad-email", "unverified-email"),
)
async def test_exchange_rejects_bad_oidc_before_database_mutation(
    monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any]
) -> None:
    _configure(monkeypatch)
    _oidc(monkeypatch, claims)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/exchange",
            json=_body(),
            headers={"Authorization": "Bearer oidc"},
        )
    assert response.status_code in {401, 403}
    assert db.commits == db.rollbacks == 0


async def test_exchange_binding_mismatch_does_not_consume(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )
    calls = 0

    async def consume(*_args: object, **_kwargs: object) -> QaCapability:
        nonlocal calls
        calls += 1
        raise AssertionError

    monkeypatch.setattr(qa_router, "consume_qa_grant", consume)
    db = _Db()
    application = _application(db, _user())
    body = _body()
    body["release_sha"] = "d" * 40
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/exchange",
            json=body,
            headers={"Authorization": "Bearer oidc"},
        )
    assert response.status_code == 409
    assert calls == db.commits == db.rollbacks == 0


async def test_exchange_success_is_no_store_and_passes_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )
    captured: dict[str, object] = {}

    async def consume(_db: object, **kwargs: object) -> QaCapability:
        captured.update(kwargs)
        now = datetime.now(UTC)
        return QaCapability(
            "capability",
            now + timedelta(minutes=5),
            now + timedelta(hours=2),
            _user().id,
            "qa@example.test",
        )

    monkeypatch.setattr(qa_router, "consume_qa_grant", consume)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/exchange",
            json=_body(),
            headers={"Authorization": "Bearer oidc"},
        )
    assert response.status_code == 200
    assert captured["request_nonce"] == uuid.UUID(_NONCE)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == 1
