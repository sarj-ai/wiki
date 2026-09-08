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
from app.document_transform.application import TransformSubmission
from app.document_transform.qa_broker import QaCanaryPlan
from app.document_transform.qa_grants import (
    IssuedQaGrant,
    QaAuthority,
    QaCapability,
    QaGrantError,
)
from app.document_transform.schemas import OutputStrategy, TransformMode
from app.identifiers import (
    AuthSessionId,
    FileId,
    GoogleUserId,
    IdempotencyKey,
    QaPlanId,
    TaskId,
    UserId,
)
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId
from app.models.user import User
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.internal_auth import GoogleOidcTaskRequestAuthenticator
from app.tasks.schemas import PendingTaskRead, TaskKind, TaskStatus


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


def _bootstrap_body() -> dict[str, str]:
    return _issuer_body() | {
        "execution_name": "job/executions/one",
        "request_nonce": _NONCE,
        "run_id": "55555555-5555-4555-8555-555555555555",
    }


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


def test_bootstrap_has_no_production_prepare_or_bind_route() -> None:
    paths = {
        getattr(route, "path", "")
        for router in (qa_router.issuer_router, qa_router.workload_router)
        for route in router.routes
    }
    assert "/document-transforms/qa/runs" not in paths
    assert "/internal/document-transform-qa/bootstrap/bind" not in paths
    assert "/internal/document-transform-qa/bootstrap" in paths


async def test_bootstrap_authenticates_before_parsing_or_reflecting_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    db = _Db()
    application = _application(db, _user())
    secret = "not-json-secret-that-must-not-be-reflected"
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/bootstrap",
            content=("{" + secret).encode(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 401
    assert secret not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0


async def test_bootstrap_authenticated_malformed_json_is_generic_and_no_store(
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
    secret = "not-json-secret-that-must-not-be-reflected"
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/bootstrap",
            content=("{" + secret).encode(),
            headers={"Authorization": "Bearer oidc", "Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert response.json() == {"detail": "QA request is invalid"}
    assert secret not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == db.rollbacks == 0


async def test_bootstrap_success_commits_exact_binding_and_is_no_store(
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

    async def bootstrap(_db: object, **kwargs: object) -> QaCapability:
        captured.update(kwargs)
        now = datetime.now(UTC)
        return QaCapability(
            "server-derived-capability",
            now + timedelta(minutes=5),
            now + timedelta(hours=2),
            _user().id,
            "qa@example.test",
        )

    monkeypatch.setattr(qa_router, "bootstrap_qa_run", bootstrap)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/bootstrap",
            json=_bootstrap_body(),
            headers={"Authorization": "Bearer oidc"},
        )
    assert response.status_code == 200
    assert captured["run_id"] == uuid.UUID(_bootstrap_body()["run_id"])
    assert captured["execution_name"] == "job/executions/one"
    assert captured["request_nonce"] == uuid.UUID(_NONCE)
    assert captured["release_sha"] == _RELEASE
    assert captured["image_digest"] == _DIGEST
    assert captured["source_folder_id"] == "source"
    assert captured["destination_folder_id"] == "destination"
    assert captured["mode"] is TransformMode.BOTH
    assert response.json()["capability_token"] == "server-derived-capability"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == 1
    assert db.rollbacks == 0


async def test_bootstrap_invalid_run_is_generic_no_store_and_does_not_reflect_input(
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

    async def bootstrap(*_args: object, **_kwargs: object) -> QaCapability:
        raise QaGrantError

    monkeypatch.setattr(qa_router, "bootstrap_qa_run", bootstrap)
    db = _Db()
    application = _application(db, _user())
    secret_execution = "job/executions/sensitive-name"
    body = _bootstrap_body() | {"execution_name": secret_execution}
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/bootstrap",
            json=body,
            headers={"Authorization": "Bearer oidc"},
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "QA credential is invalid"}
    assert secret_execution not in response.text
    assert body["run_id"] not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == 0
    assert db.rollbacks == 1


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


async def test_canary_submit_reauthorizes_and_returns_only_bounded_admission(
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
    submit_calls = 0
    operation = uuid.UUID("55555555-5555-4555-8555-555555555555")
    task_id = TaskId(uuid.UUID("66666666-6666-4666-8666-666666666666"))

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
        async def submit(self, authority: QaAuthority, **kwargs: object) -> TransformSubmission:
            nonlocal submit_calls
            submit_calls += 1
            assert authority.source_folder_id == "source"
            if kwargs["plan_id"] != operation:
                raise QaGrantError
            return TransformSubmission(
                task=PendingTaskRead(
                    id=task_id,
                    kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
                    status=TaskStatus.PENDING,
                    result=None,
                    resource_id=None,
                    error_code=None,
                    error_detail=None,
                    attempt_count=0,
                    started_at=None,
                    finished_at=None,
                    created_at=now,
                ),
                replayed=submit_calls > 1,
            )

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    application.dependency_overrides[task_service_dep] = object
    application.dependency_overrides[task_enqueuer_dep] = object
    body = _issuer_body() | {
        "execution_name": "job/executions/one",
        "plan_id": str(operation),
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        first = await http.post(
            "/internal/document-transform-qa/canary/submit",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )
        replay = await http.post(
            "/internal/document-transform-qa/canary/submit",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )
        stale = await http.post(
            "/internal/document-transform-qa/canary/submit",
            json=body | {"plan_id": "77777777-7777-4777-8777-777777777777"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert first.status_code == replay.status_code == 202
    assert stale.status_code == 409
    assert authorization_calls == submit_calls == 3
    assert first.json()["task_id"] == replay.json()["task_id"] == str(task_id)
    assert first.json()["task_status"] == replay.json()["task_status"] == "pending"
    assert not first.json()["replayed"]
    assert replay.json()["replayed"]
    assert first.json()["output_strategy"] == "copy"
    assert "source_file_id" not in first.text
    assert first.headers["cache-control"] == replay.headers["cache-control"] == "no-store"
    assert stale.headers["cache-control"] == "no-store"
    assert stale.headers["pragma"] == "no-cache"


async def test_canary_submit_rejects_caller_selected_file_before_capability_authorization(
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
        async def submit(self, *_args: object, **_kwargs: object) -> TransformSubmission:
            pytest.fail("caller-selected source reached task submission")

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    application.dependency_overrides[task_service_dep] = object
    application.dependency_overrides[task_enqueuer_dep] = object
    body = _issuer_body() | {
        "execution_name": "job/executions/one",
        "plan_id": "55555555-5555-4555-8555-555555555555",
        "source_file_id": "attacker-selected-doc",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/submit",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 422
    assert "attacker-selected-doc" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert db.commits == db.rollbacks == 0
