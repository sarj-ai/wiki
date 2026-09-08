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
from app.document_transform.qa_broker import QaCanaryPlan
from app.document_transform.qa_grants import (
    IssuedQaGrant,
    QaAuthority,
    QaCapability,
    QaGrantError,
)
from app.document_transform.schemas import OutputStrategy, TransformMode
from app.identifiers import AuthSessionId, FileId, GoogleUserId, IdempotencyKey, QaPlanId, UserId
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


async def test_canary_session_requires_capability_without_database_authorization(
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
    calls = 0

    async def authorize(*_args: object, **_kwargs: object) -> QaAuthority:
        nonlocal calls
        calls += 1
        raise AssertionError

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    body = _issuer_body() | {"execution_name": "job/executions/one"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/session",
            json=body,
            headers={"Authorization": "Bearer oidc"},
        )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert calls == db.commits == db.rollbacks == 0


async def test_canary_session_returns_only_bound_scope_and_is_no_store(
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
    now = datetime.now(UTC)

    async def authorize(_db: object, **kwargs: object) -> QaAuthority:
        captured.update(kwargs)
        return QaAuthority(
            grant_id=DocumentTransformQaGrantId(uuid.UUID("44444444-4444-4444-8444-444444444444")),
            user_id=_user().id,
            user_email="qa@example.test",
            execution_name="job/executions/one",
            release_sha=_RELEASE,
            image_digest=_DIGEST,
            source_folder_id=FileId("source"),
            destination_folder_id=FileId("destination"),
            mode=TransformMode.BOTH,
            expires_at=now + timedelta(minutes=5),
            job_expires_at=now + timedelta(hours=2),
        )

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    body = _issuer_body() | {"execution_name": "job/executions/one"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/session",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )
    assert response.status_code == 200
    assert captured["capability_token"] == "z" * 43
    assert captured["execution_name"] == "job/executions/one"
    assert response.json()["authorized"] is True
    assert "capability_token" not in response.text
    assert "grant_id" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0


async def test_canary_session_rejects_invalid_capability_without_echoing_it(
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

    async def authorize(*_args: object, **_kwargs: object) -> QaAuthority:
        raise QaGrantError

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    credential = "z" * 43
    body = _issuer_body() | {"execution_name": "job/executions/one"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/session",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": credential,
            },
        )
    assert response.status_code == 403
    assert credential not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.parametrize("credential", ["short-secret", "z" * 257], ids=("short", "oversize"))
async def test_canary_session_rejects_malformed_secret_without_echo_and_with_no_store(
    monkeypatch: pytest.MonkeyPatch, credential: str
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
    db = _Db()
    application = _application(db, _user())
    body = _issuer_body() | {"execution_name": "job/executions/one"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/session",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": credential,
            },
        )
    assert response.status_code == 403
    assert credential not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0


async def test_canary_session_authenticates_before_parsing_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/session",
            content=b"{not-json",
            headers={
                "Content-Type": "application/json",
                "X-Wiki-QA-Capability": "secret-that-must-not-be-reflected",
            },
        )
    assert response.status_code == 401
    assert "secret-that-must-not-be-reflected" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0


async def test_canary_session_authenticated_malformed_json_is_no_store(
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
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/session",
            content=b"{not-json",
            headers={
                "Authorization": "Bearer oidc",
                "Content-Type": "application/json",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0


async def test_canary_session_binding_mismatch_is_no_store(
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
    db = _Db()
    application = _application(db, _user())
    body = _issuer_body() | {
        "execution_name": "job/executions/one",
        "release_sha": "d" * 40,
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/session",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0


async def test_canary_plan_reauthorizes_and_returns_server_selected_copy(
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
    now = datetime.now(UTC)
    authorization_calls = 0
    plan_calls = 0

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
        nonlocal authorization_calls
        authorization_calls += 1
        return QaAuthority(
            grant_id=DocumentTransformQaGrantId(uuid.UUID("44444444-4444-4444-8444-444444444444")),
            user_id=_user().id,
            user_email="qa@example.test",
            execution_name="job/executions/one",
            release_sha=_RELEASE,
            image_digest=_DIGEST,
            source_folder_id=FileId("source"),
            destination_folder_id=FileId("destination"),
            mode=TransformMode.BOTH,
            expires_at=now + timedelta(minutes=5),
            job_expires_at=now + timedelta(hours=2),
        )

    class Broker:
        async def plan(self, authority: QaAuthority) -> QaCanaryPlan:
            nonlocal plan_calls
            plan_calls += 1
            assert authority.source_folder_id == "source"
            operation = uuid.UUID("55555555-5555-4555-8555-555555555555")
            return QaCanaryPlan(
                plan_id=QaPlanId(operation),
                idempotency_key=IdempotencyKey(operation),
                source_file_id=FileId("server-selected-doc"),
                source_name="Security Review",
                source_modified_time="2026-09-07T12:00:00Z",
                output_strategy=OutputStrategy.COPY,
            )

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    body = _issuer_body() | {"execution_name": "job/executions/one"}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/plan",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 200
    assert authorization_calls == plan_calls == 1
    assert response.json()["source_file_id"] == "server-selected-doc"
    assert response.json()["output_strategy"] == "copy"
    assert response.json()["plan_id"] == response.json()["idempotency_key"]
    assert "z" * 43 not in response.text
    assert "grant_id" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


async def test_canary_plan_rejects_caller_selected_file_before_authorization(
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

    async def authorize(*_args: object, **_kwargs: object) -> QaAuthority:
        pytest.fail("caller-selected source reached capability authorization")

    class Broker:
        async def plan(self, _authority: QaAuthority) -> QaCanaryPlan:
            pytest.fail("caller-selected source reached Drive planning")

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    body = _issuer_body() | {
        "execution_name": "job/executions/one",
        "source_file_id": "attacker-selected-doc",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/plan",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 422
    assert "attacker-selected-doc" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0
