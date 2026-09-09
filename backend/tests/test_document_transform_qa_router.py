from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
import uuid

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import get_current_user
from app.config import settings
from app.csrf import CookieOriginMiddleware
from app.database import get_db
from app.document_transform import qa_router
from app.document_transform.application import TransformSubmission
from app.document_transform.qa_broker import (
    QaCanaryPlan,
    QaCorpusCampaignCandidate,
    QaDestinationPermissionProof,
)
from app.document_transform.qa_campaigns import (
    CreatedQaCorpusCampaign,
    QaCorpusCampaignError,
    QaCorpusCampaignMemberRead,
    QaCorpusCampaignRead,
)
from app.document_transform.qa_corpus import QaCorpusPlan, QaCorpusSourceFolder
from app.document_transform.qa_grants import (
    IssuedQaGrant,
    PreparedQaLaunch,
    QaAuthority,
    QaBootstrapResult,
    QaCapability,
    QaGrantError,
    QaSubmittedCanary,
)
from app.document_transform.qa_launcher import QaExecutionAttestation
from app.document_transform.schemas import (
    DocumentOutput,
    DocumentTransformResult,
    FindingSeverity,
    OutputStrategy,
    QaCopyPermissionEvidence,
    QaFilePermissionEvidence,
    TransformFinding,
    TransformMode,
    ValidationSummary,
)
from app.files.schemas import FileResponse
from app.identifiers import (
    AuthSessionId,
    DocsRevisionId,
    DriveId,
    FileId,
    GoogleUserId,
    IdempotencyKey,
    QaPlanId,
    QaRunId,
    TaskId,
    UserId,
)
from app.models.document_transform_qa_corpus import (
    DocumentTransformQaCorpusCampaignId,
    DocumentTransformQaCorpusMemberId,
)
from app.models.document_transform_qa_grant import DocumentTransformQaGrantId
from app.models.user import User
from app.tasks.dependencies import task_enqueuer_dep, task_service_dep
from app.tasks.internal_auth import GoogleOidcTaskRequestAuthenticator
from app.tasks.schemas import PendingTaskRead, SucceededTaskRead, TaskKind, TaskStatus


_RELEASE = "a" * 40
_DIGEST = "sha256:" + "b" * 64
_AUDIENCE = "https://api.example.test/internal/document-transform-qa/exchange"
_SERVICE_ACCOUNT = "qa@example-project.iam.gserviceaccount.com"
_NONCE = "11111111-1111-4111-8111-111111111111"
_IDEMPOTENCY = "019c4f83-9556-7c56-9a5a-7583c478ce24"
_OUTPUT_FILE_ID = FileId("server-derived-output")


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


def _launcher_renewal_body() -> dict[str, str]:
    return {key: value for key, value in _body(operation="renew").items() if key != "operation"}


def _issuer_body() -> dict[str, str]:
    excluded = {"credential", "execution_name", "request_nonce", "operation"}
    return {key: value for key, value in _body().items() if key not in excluded}


def _bootstrap_body() -> dict[str, str]:
    return {"execution_name": "job/executions/one", "request_nonce": _NONCE}


def _corpus_plan() -> QaCorpusPlan:
    return QaCorpusPlan(
        plan_id=QaPlanId(uuid.UUID("55555555-5555-4555-8555-555555555555")),
        source_folder_id=FileId("source"),
        source_drive_id=DriveId("drive"),
        source_folder=QaCorpusSourceFolder(
            file_id=FileId("source"),
            name="Security",
            mime_type="application/vnd.google-apps.folder",
            parent_folder_id=None,
            drive_id=DriveId("drive"),
            modified_time=None,
            version="1",
        ),
        documents=(),
        unsupported_files=(),
        member_versions=(),
        observed_item_count=0,
        traversed_folder_count=1,
    )


def _corpus_campaign_read() -> QaCorpusCampaignRead:
    campaign_id = DocumentTransformQaCorpusCampaignId(
        uuid.UUID("77777777-7777-4777-8777-777777777777")
    )
    members = tuple(
        QaCorpusCampaignMemberRead(
            id=DocumentTransformQaCorpusMemberId(uuid.uuid5(uuid.NAMESPACE_URL, str(ordinal))),
            ordinal=ordinal,
            source_file_id=FileId(f"document-{ordinal}"),
            source_name=f"Security Document {ordinal}",
            mime_type="application/vnd.google-apps.document",
            source_parent_folder_id=FileId("source"),
            source_version=str(ordinal + 1),
            source_modified_time=f"2026-09-08T12:{ordinal:02}:00Z",
            task_plan_id=QaPlanId(uuid.uuid5(uuid.NAMESPACE_OID, str(ordinal))),
        )
        for ordinal in range(20)
    )
    return QaCorpusCampaignRead(
        id=campaign_id,
        corpus_plan_id=QaPlanId(uuid.UUID("55555555-5555-4555-8555-555555555555")),
        state="canary_reserved",
        source_folder_id=FileId("source"),
        destination_folder_id=FileId("destination"),
        mode="both",
        created_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
        members=members,
    )


class _AttestedLauncher:
    async def attest_execution(self, execution_name: str) -> QaExecutionAttestation:
        return QaExecutionAttestation(
            execution_name=execution_name,
            execution_uid=uuid.UUID("12345678-1234-4234-8234-123456789abc"),
            job_name="projects/test/locations/me-central2/jobs/qa",
            image="image@" + _DIGEST,
            release_sha=_RELEASE,
        )


def _application(db: _Db, user: User) -> FastAPI:
    application = FastAPI()
    application.add_middleware(CookieOriginMiddleware, allowed_origin=settings.frontend_origin)
    application.include_router(qa_router.issuer_router)
    application.include_router(qa_router.workload_router)
    application.dependency_overrides[get_current_user] = lambda: user
    application.dependency_overrides[get_db] = lambda: db
    application.dependency_overrides[qa_router.qa_launcher_dep] = _AttestedLauncher
    return application


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RELEASE_SHA", _RELEASE)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST", _DIGEST)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_AUDIENCE", _AUDIENCE)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT", _SERVICE_ACCOUNT)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_RELEASE_SHA", _RELEASE)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS", ("qa@example.test",))


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
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", None)
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


async def test_launcher_mode_disables_legacy_grant_issuer_before_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", "configured-job")
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/grants",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin},
            cookies={"access_token": "cookie"},
        )

    assert response.status_code == 410
    assert response.headers["cache-control"] == "no-store"
    assert db.commits == db.rollbacks == 0


async def test_launcher_mode_retires_legacy_issuer_before_sensitive_body_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", "configured-job")
    db = _Db()
    application = _application(db, _user())
    secret = "malformed-legacy-grant-secret"
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/grants",
            content=("{" + secret).encode(),
            headers={"Content-Type": "application/json", "Origin": settings.frontend_origin},
            cookies={"access_token": "cookie"},
        )

    assert response.status_code == 410
    assert secret not in response.text
    assert db.commits == db.rollbacks == 0


async def test_backend_owned_launcher_prepares_launches_attests_then_binds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    user = _user()
    session_id = AuthSessionId(uuid.UUID("33333333-3333-4333-8333-333333333333"))
    run_id = uuid.UUID("55555555-5555-4555-8555-555555555555")
    execution_name = "projects/test/locations/me-central2/jobs/qa/executions/one"
    monkeypatch.setattr(
        qa_router,
        "decode_token",
        lambda _token: SimpleNamespace(type="access", sub=user.id, sid=session_id, tv=3),
    )
    captured: dict[str, object] = {}
    captured_bind: dict[str, object] = {}

    async def issue(_db: object, **_kwargs: object) -> IssuedQaGrant:
        return IssuedQaGrant(
            grant_id=DocumentTransformQaGrantId(uuid.UUID("44444444-4444-4444-8444-444444444444")),
            token="never-transported-grant-token",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    async def prepare(_db: object, **kwargs: object) -> PreparedQaLaunch:
        captured["prepare"] = kwargs
        return PreparedQaLaunch(
            run_id=QaRunId(run_id),
            state="launch_claimed",
            execution_name=None,
            owns_launch_claim=True,
        )

    async def bind(_db: object, **kwargs: object) -> None:
        captured_bind.update(kwargs)

    class _Launcher:
        async def launch(self) -> QaExecutionAttestation:
            return QaExecutionAttestation(
                execution_name=execution_name,
                execution_uid=uuid.UUID("12345678-1234-4234-8234-123456789abc"),
                job_name="projects/test/locations/me-central2/jobs/qa",
                image="image@" + _DIGEST,
                release_sha=_RELEASE,
            )

    monkeypatch.setattr(qa_router, "issue_qa_grant", issue)
    monkeypatch.setattr(qa_router, "prepare_idempotent_qa_launch", prepare)
    monkeypatch.setattr(qa_router, "bind_qa_bootstrap_execution", bind)
    db = _Db()
    application = _application(db, user)
    application.dependency_overrides[qa_router.qa_launcher_dep] = _Launcher
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/launch",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin, "Idempotency-Key": _IDEMPOTENCY},
            cookies={"access_token": "cookie"},
        )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": str(run_id),
        "execution_name": execution_name,
        "status": "execution_bound",
    }
    assert db.commits == 2
    assert captured_bind["execution_name"] == execution_name
    assert "never-transported-grant-token" not in response.text


async def test_backend_owned_launcher_is_default_off_before_ledger_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", None)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides.pop(qa_router.qa_launcher_dep)
    application.dependency_overrides[qa_router.http_client_dep] = lambda: None
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/launch",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin, "Idempotency-Key": _IDEMPOTENCY},
            cookies={"access_token": "cookie"},
        )
    assert response.status_code == 503
    assert db.commits == db.rollbacks == 0


async def test_launcher_rejects_non_operator_before_ledger_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS", ("other@sarj.ai",))
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/launch",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin, "Idempotency-Key": _IDEMPOTENCY},
            cookies={"access_token": "cookie"},
        )
    assert response.status_code == 403
    assert db.commits == db.rollbacks == 0


@pytest.mark.parametrize("key", [None, "not-a-uuid", "11111111-1111-4111-8111-111111111111"])
async def test_launcher_requires_uuid7_idempotency_before_ledger_mutation(
    monkeypatch: pytest.MonkeyPatch, key: str | None
) -> None:
    _configure(monkeypatch)
    db = _Db()
    application = _application(db, _user())
    headers = {"Origin": settings.frontend_origin}
    if key is not None:
        headers["Idempotency-Key"] = key
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/launch",
            json=_issuer_body(),
            headers=headers,
            cookies={"access_token": "cookie"},
        )
    assert response.status_code == 422
    assert db.commits == db.rollbacks == 0


async def test_launcher_global_single_flight_collision_never_starts_job(
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

    async def prepare(*_args: object, **_kwargs: object) -> PreparedQaLaunch:
        statement = "active QA launch"
        raise IntegrityError(statement, {}, RuntimeError())

    monkeypatch.setattr(qa_router, "prepare_idempotent_qa_launch", prepare)
    db = _Db()
    application = _application(db, user)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/launch",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin, "Idempotency-Key": _IDEMPOTENCY},
            cookies={"access_token": "cookie"},
        )
    assert response.status_code == 409
    assert db.commits == 0
    assert db.rollbacks == 1


async def test_launcher_same_key_replay_cannot_launch_while_claim_is_in_progress(
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

    async def prepare(*_args: object, **_kwargs: object) -> PreparedQaLaunch:
        return PreparedQaLaunch(
            run_id=QaRunId(uuid.UUID("55555555-5555-4555-8555-555555555555")),
            state="launch_claimed",
            execution_name=None,
            owns_launch_claim=False,
        )

    class _NeverLauncher:
        async def launch(self) -> QaExecutionAttestation:
            raise AssertionError

    monkeypatch.setattr(qa_router, "prepare_idempotent_qa_launch", prepare)
    db = _Db()
    application = _application(db, user)
    application.dependency_overrides[qa_router.qa_launcher_dep] = _NeverLauncher
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/document-transforms/qa/launch",
            json=_issuer_body(),
            headers={"Origin": settings.frontend_origin, "Idempotency-Key": _IDEMPOTENCY},
            cookies={"access_token": "cookie"},
        )
    assert response.status_code == 409
    assert response.json() == {"detail": "QA launch is in progress"}
    assert response.headers["retry-after"] == "2"
    assert response.headers["cache-control"] == "no-store"
    assert db.commits == 1


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
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", None)
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


async def test_launcher_mode_disables_legacy_exchange_before_database_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", "configured-job")
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
            "/internal/document-transform-qa/exchange",
            json=_body(),
            headers={"Authorization": "Bearer oidc"},
        )

    assert response.status_code == 410
    assert response.headers["cache-control"] == "no-store"
    assert db.commits == db.rollbacks == 0


async def test_launcher_mode_retires_legacy_exchange_before_sensitive_body_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", "configured-job")
    db = _Db()
    application = _application(db, _user())
    secret = "malformed-legacy-exchange-secret"
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/exchange",
            content=("{" + secret).encode(),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 410
    assert secret not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert db.commits == db.rollbacks == 0


@pytest.mark.parametrize(
    "query",
    ["operation=consume", "operation=renew&extra=1", "operation=renew&operation=renew"],
)
async def test_launcher_mode_rejects_wrong_exchange_selector_before_oidc_or_body(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", "configured-job")

    def unexpected_authenticator(**_kwargs: object) -> object:
        pytest.fail("wrong launcher exchange selector reached workload authentication")

    monkeypatch.setattr(qa_router, "GoogleOidcTaskRequestAuthenticator", unexpected_authenticator)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            f"/internal/document-transform-qa/exchange?{query}",
            content=b"{sensitive malformed body",
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 410
    assert response.headers["cache-control"] == "no-store"
    assert db.commits == db.rollbacks == 0


async def test_launcher_mode_exchange_allows_only_strict_submitted_task_renewal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_JOB", "configured-job")
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

    async def renew(_db: object, **kwargs: object) -> QaCapability:
        captured.update(kwargs)
        now = datetime.now(UTC)
        return QaCapability(
            "renewed-capability",
            now + timedelta(minutes=5),
            now + timedelta(hours=1),
            _user().id,
            "qa@example.test",
        )

    monkeypatch.setattr(qa_router, "renew_qa_capability", renew)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/exchange?operation=renew",
            json=_launcher_renewal_body(),
            headers={"Authorization": "Bearer oidc"},
        )
        rejected_extra = await http.post(
            "/internal/document-transform-qa/exchange?operation=renew",
            json=_body(operation="renew"),
            headers={"Authorization": "Bearer oidc"},
        )

    assert response.status_code == 200
    assert response.json()["capability_token"] == "renewed-capability"
    assert captured["capability_token"] == "c" * 43
    assert captured["execution_name"] == "job/executions/one"
    assert captured["require_submitted_task"] is True
    assert rejected_extra.status_code == 422
    assert db.commits == 1
    assert db.rollbacks == 0


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

    async def read_identity(_db: object) -> object:
        return SimpleNamespace(execution_name="job/executions/one")

    async def bootstrap(_db: object, **kwargs: object) -> QaBootstrapResult:
        captured.update(kwargs)
        now = datetime.now(UTC)
        return QaBootstrapResult(
            capability=QaCapability(
                "server-derived-capability",
                now + timedelta(minutes=5),
                now + timedelta(hours=2),
                _user().id,
                "qa@example.test",
            ),
            execution_name="job/executions/one",
            release_sha=_RELEASE,
            image_digest=_DIGEST,
            source_folder_id=FileId("source"),
            destination_folder_id=FileId("destination"),
            mode=TransformMode.BOTH,
        )

    monkeypatch.setattr(qa_router, "read_active_qa_execution", read_identity)
    monkeypatch.setattr(qa_router, "bootstrap_attested_qa_execution", bootstrap)
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
    assert captured["request_nonce"] == uuid.UUID(_NONCE)
    assert captured["release_sha"] == _RELEASE
    assert captured["image_digest"] == _DIGEST
    assert response.json()["capability_token"] == "server-derived-capability"
    assert response.json()["execution_name"] == "job/executions/one"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert db.commits == 1
    assert db.rollbacks == 0


async def test_bootstrap_stale_execution_selector_cannot_burn_bound_run_capability(
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

    async def read_identity(_db: object) -> object:
        return SimpleNamespace(execution_name="job/executions/current")

    async def bootstrap(*_args: object, **_kwargs: object) -> QaBootstrapResult:
        raise AssertionError

    monkeypatch.setattr(qa_router, "read_active_qa_execution", read_identity)
    monkeypatch.setattr(qa_router, "bootstrap_attested_qa_execution", bootstrap)
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
                destination_version="17",
                destination_permission_sha256="c" * 64,
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
    assert response.json()["destination_version"] == "17"
    assert response.json()["destination_permission_sha256"] == "c" * 64
    assert "z" * 43 not in response.text
    assert "grant_id" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.parametrize(
    ("configured_source", "expected_status"),
    [(None, 503), ("different-security-folder", 409)],
)
async def test_corpus_plan_is_default_off_and_rejects_nonconfigured_source(
    monkeypatch: pytest.MonkeyPatch,
    configured_source: str | None,
    expected_status: int,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(
        settings,
        "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID",
        configured_source,
    )
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

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
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
        async def plan_corpus(self, _authority: QaAuthority) -> QaCorpusPlan:
            pytest.fail("Drive must not be read for a disabled or mismatched source")

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    application = _application(_Db(), _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/plan",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == expected_status
    assert response.headers["cache-control"] == "no-store"


async def test_corpus_plan_uses_capability_bound_configured_security_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID", "source")
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

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
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
        async def plan_corpus(self, authority: QaAuthority) -> QaCorpusPlan:
            assert authority.source_folder_id == "source"
            return QaCorpusPlan(
                plan_id=QaPlanId(uuid.UUID("55555555-5555-4555-8555-555555555555")),
                source_folder_id=FileId("source"),
                source_drive_id=DriveId("drive"),
                source_folder=QaCorpusSourceFolder(
                    file_id=FileId("source"),
                    name="Security",
                    mime_type="application/vnd.google-apps.folder",
                    parent_folder_id=None,
                    drive_id=DriveId("drive"),
                    modified_time=None,
                    version="1",
                ),
                documents=(),
                unsupported_files=(),
                member_versions=(),
                observed_item_count=0,
                traversed_folder_count=1,
            )

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    application = _application(_Db(), _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/plan",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 200
    assert response.json()["source_folder_id"] == "source"
    assert "user_email" not in response.text
    assert "destination_folder_id" not in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_corpus_plan_returns_generic_bounded_conflict_for_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID", "source")
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
        return _result_authority(datetime.now(UTC))

    class Broker:
        async def plan_corpus(self, _authority: QaAuthority) -> QaCorpusPlan:
            raise QaGrantError from TimeoutError("private provider and credential detail")

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    application = _application(_Db(), _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/plan",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "QA corpus could not be planned"}
    assert len(response.content) < 256
    assert "private provider" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


async def test_corpus_campaign_create_reauthorizes_after_drive_reads_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID", "source")
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )
    authority = _result_authority(datetime.now(UTC))
    campaign = _corpus_campaign_read()
    events: list[str] = []

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
        events.append("authorize")
        return authority

    destination = QaDestinationPermissionProof(
        file_id=FileId("destination"),
        mime_type="application/vnd.google-apps.folder",
        parent_folder_id=FileId("root"),
        version="17",
        sha256="c" * 64,
        observed_at=datetime.now(UTC),
    )

    class Broker:
        async def plan_corpus_campaign(self, received: QaAuthority) -> QaCorpusCampaignCandidate:
            assert received is authority
            events.append("drive")
            return QaCorpusCampaignCandidate(
                plan=_corpus_plan(),
                destination=destination,
                corpus_plan_sha256="d" * 64,
                source_root_sha256="e" * 64,
                destination_identity_sha256="f" * 64,
            )

    async def create(_db: object, **kwargs: object) -> CreatedQaCorpusCampaign:
        events.append("create")
        assert kwargs == {
            "authority": authority,
            "plan": _corpus_plan(),
            "destination": destination,
        }
        return CreatedQaCorpusCampaign(campaign=campaign, replayed=False)

    async def load(_db: object, **kwargs: object) -> None:
        events.append("lookup")
        assert kwargs == {"authority": authority}

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    monkeypatch.setattr(qa_router, "load_qa_corpus_campaign_for_authority", load)
    monkeypatch.setattr(qa_router, "create_or_load_qa_corpus_campaign", create)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/campaign",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 200
    assert events == ["authorize", "lookup", "drive", "authorize", "create"]
    assert db.commits == 1
    assert db.rollbacks == 0
    assert response.json()["campaign_id"] == str(campaign.id)
    assert response.json()["replayed"] is False
    assert len(response.json()["members"]) == 20
    assert "z" * 43 not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


async def test_corpus_campaign_create_replays_from_database_without_drive_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID", "source")
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )
    authority = _result_authority(datetime.now(UTC))
    campaign = _corpus_campaign_read()
    authorization_calls = 0

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
        nonlocal authorization_calls
        authorization_calls += 1
        return authority

    async def load(_db: object, **kwargs: object) -> QaCorpusCampaignRead:
        assert kwargs == {"authority": authority}
        return campaign

    class Broker:
        async def plan_corpus_campaign(self, _authority: QaAuthority) -> object:
            pytest.fail("Drive was read during an exact database replay")

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    monkeypatch.setattr(qa_router, "load_qa_corpus_campaign_for_authority", load)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/campaign",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 200
    assert response.json()["replayed"] is True
    assert authorization_calls == 1
    assert db.commits == 1
    assert db.rollbacks == 0


async def test_corpus_campaign_create_stops_when_post_drive_reauthorization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID", "source")
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )
    authority = _result_authority(datetime.now(UTC))
    authorization_calls = 0

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
        nonlocal authorization_calls
        authorization_calls += 1
        if authorization_calls == 2:
            raise QaGrantError
        return authority

    class Broker:
        async def plan_corpus_campaign(self, _authority: QaAuthority) -> object:
            return SimpleNamespace(plan=_corpus_plan(), destination=object())

    async def create(*_args: object, **_kwargs: object) -> object:
        pytest.fail("campaign creation reached after capability expiry")

    async def load(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    monkeypatch.setattr(qa_router, "load_qa_corpus_campaign_for_authority", load)
    monkeypatch.setattr(qa_router, "create_or_load_qa_corpus_campaign", create)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/campaign",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 403
    assert authorization_calls == 2
    assert db.commits == 0
    assert db.rollbacks == 1
    assert response.json() == {"detail": "QA credential is invalid"}
    assert response.headers["cache-control"] == "no-store"


async def test_corpus_campaign_create_returns_generic_no_store_service_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID", "source")
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
        return _result_authority(datetime.now(UTC))

    async def fail(**_kwargs: object) -> CreatedQaCorpusCampaign:
        raise QaCorpusCampaignError from RuntimeError("private provider and file detail")

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    monkeypatch.setattr(qa_router, "_create_or_replay_corpus_campaign", fail)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = object
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/campaign",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "QA corpus campaign could not be created"}
    assert "private provider" not in response.text
    assert db.commits == 0
    assert db.rollbacks == 1
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


async def test_corpus_campaign_read_is_owner_and_capability_scope_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID", "source")
    _oidc(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": _AUDIENCE,
            "email": _SERVICE_ACCOUNT,
            "email_verified": True,
        },
    )
    authority = _result_authority(datetime.now(UTC))
    campaign = _corpus_campaign_read()

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
        return authority

    async def read(_db: object, **kwargs: object) -> QaCorpusCampaignRead:
        assert kwargs == {"authority": authority}
        return campaign

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    monkeypatch.setattr(qa_router, "load_qa_corpus_campaign_for_authority", read)
    db = _Db()
    application = _application(db, _user())
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/corpus/campaign/read",
            json=_issuer_body() | {"execution_name": "job/executions/one"},
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 200
    assert response.json()["campaign_id"] == str(campaign.id)
    assert response.json()["source_folder_id"] == "source"
    assert response.json()["destination_folder_id"] == "destination"
    assert "user_email" not in response.text
    assert db.commits == db.rollbacks == 0
    assert response.headers["cache-control"] == "no-store"


def test_corpus_campaign_response_is_bounded_to_sixty_four_kibibytes() -> None:
    response = qa_router._campaign_response(_corpus_campaign_read())
    oversized = response.model_copy(
        update={
            "members": tuple(
                member.model_copy(update={"source_name": "\U0001f512" * 1024})
                for member in response.members
            )
        }
    )

    with pytest.raises(QaCorpusCampaignError):
        qa_router._bounded_campaign_response(oversized)


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


async def test_canary_plan_permission_failure_is_generic_and_no_store(
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
    sensitive_error_detail = "domain-reader@example.test"

    async def authorize(_db: object, **_kwargs: object) -> QaAuthority:
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
        async def plan(self, _authority: QaAuthority) -> QaCanaryPlan:
            raise QaGrantError(sensitive_error_detail)

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

    assert response.status_code == 409
    assert response.json() == {"detail": "QA canary could not be planned"}
    assert sensitive_error_detail not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


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
    bind_calls = 0
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

    async def bind(_db: object, **kwargs: object) -> QaSubmittedCanary:
        nonlocal bind_calls
        bind_calls += 1
        assert kwargs["plan_id"] == operation
        assert kwargs["task_id"] == task_id
        return QaSubmittedCanary(plan_id=QaPlanId(operation), task_id=task_id)

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    monkeypatch.setattr(qa_router, "bind_qa_canary_submission", bind)
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
    assert authorization_calls == 5
    assert submit_calls == 3
    assert bind_calls == 2
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


def _transform_result(
    *,
    operation_id: uuid.UUID | None = None,
    finding_count: int = 150,
) -> DocumentTransformResult:
    operation_id = operation_id or uuid.UUID("66666666-6666-4666-8666-666666666666")
    return DocumentTransformResult(
        operation_id=operation_id,
        source_sha256="c" * 64,
        output=DocumentOutput(
            file_id=FileId("server-derived-output"),
            name="Security Review — Polished",
            parent_folder_id=FileId("destination"),
            web_url="https://attacker.invalid/stored-provider-url",
        ),
        mode=TransformMode.BOTH,
        applied_patch_count=750,
        deterministic_patch_count=500,
        model_patch_count=250,
        promoted_heading_count=2,
        findings=[
            TransformFinding(
                code=f"finding-{index:03d}",
                detail=f"Finding {index}",
                severity=(FindingSeverity.INFORMATIONAL if index < 100 else FindingSeverity.REVIEW),
            )
            for index in range(finding_count)
        ],
        validation=ValidationSummary(
            source_unchanged=True,
            protected_content_preserved=True,
            structure_preserved=True,
            unsafe_patch_count=0,
        ),
        model_ids=[],
        tool_versions={},
        pipeline_version="qa-test-v1",
        needs_review=False,
        output_strategy=OutputStrategy.COPY,
        source_revision_id=DocsRevisionId("source-revision"),
        output_revision_id=DocsRevisionId("output-revision"),
        short_link_alias="security-review",
        short_link_status="ready",
        access_scope="owner_only",
    )


def _permission_evidence(*, operation_id: uuid.UUID | None = None) -> QaCopyPermissionEvidence:
    operation_id = operation_id or uuid.UUID("66666666-6666-4666-8666-666666666666")
    observed_at = datetime(2026, 9, 8, 1, 0, tzinfo=UTC)
    return QaCopyPermissionEvidence(
        operation_id=operation_id,
        source_sha256="c" * 64,
        output_revision_id=DocsRevisionId("output-revision"),
        destination=QaFilePermissionEvidence(
            file_id=FileId("destination"),
            mime_type="application/vnd.google-apps.folder",
            parent_folder_id=None,
            version="17",
            permission_sha256="d" * 64,
            observed_at=observed_at,
        ),
        output=QaFilePermissionEvidence(
            file_id=FileId("server-derived-output"),
            mime_type="application/vnd.google-apps.document",
            parent_folder_id=FileId("destination"),
            version="18",
            permission_sha256="e" * 64,
            observed_at=observed_at,
        ),
    )


def _succeeded_task(
    result: DocumentTransformResult,
    *,
    now: datetime,
    task_id: TaskId | None = None,
    resource_id: FileId | None = _OUTPUT_FILE_ID,
) -> SucceededTaskRead:
    resolved_task_id = task_id or TaskId(uuid.UUID("66666666-6666-4666-8666-666666666666"))
    return SucceededTaskRead(
        id=resolved_task_id,
        kind=TaskKind.FILE_DOCUMENT_TRANSFORM,
        status=TaskStatus.SUCCEEDED,
        result=result,
        resource_id=resource_id,
        error_code=None,
        error_detail=None,
        attempt_count=1,
        started_at=now,
        finished_at=now + timedelta(seconds=5),
        created_at=now - timedelta(seconds=1),
    )


def _result_authority(now: datetime) -> QaAuthority:
    return QaAuthority(
        grant_id=DocumentTransformQaGrantId(uuid.uuid7()),
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


async def test_canary_task_result_and_evidence_reauthorize_and_are_bounded_no_store(
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
    read_calls = 0
    operation = QaPlanId(uuid.UUID("55555555-5555-4555-8555-555555555555"))
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
        forged_operation = False
        task_resource_id: FileId | None = FileId("server-derived-output")
        output_available = True
        output_parent = FileId("destination")

        async def read_permission_evidence(
            self, authority: QaAuthority, **kwargs: object
        ) -> QaCopyPermissionEvidence:
            assert authority.user_id == _user().id
            assert kwargs["submission"] == QaSubmittedCanary(plan_id=operation, task_id=task_id)
            return _permission_evidence(operation_id=task_id)

        async def read_task(self, authority: QaAuthority, **kwargs: object) -> SucceededTaskRead:
            nonlocal read_calls
            read_calls += 1
            assert authority.user_id == _user().id
            assert kwargs["submission"] == QaSubmittedCanary(plan_id=operation, task_id=task_id)
            return _succeeded_task(
                _transform_result(
                    operation_id=(
                        uuid.UUID("77777777-7777-4777-8777-777777777777")
                        if self.forged_operation
                        else task_id
                    )
                ),
                now=now,
                task_id=task_id,
                resource_id=self.task_resource_id,
            )

        async def authorize_output(self, authority: QaAuthority, **kwargs: object) -> FileResponse:
            assert authority.destination_folder_id == "destination"
            if not self.output_available or self.output_parent != authority.destination_folder_id:
                raise QaGrantError
            output = kwargs["output"]
            assert isinstance(output, DocumentOutput)
            return FileResponse(
                id=output.file_id,
                name="Fresh Provider Name",
                mime_type="application/vnd.google-apps.document",
                parent_folder_id=self.output_parent,
                drive_id=None,
                modified_time="2026-09-08T01:00:00Z",
                can_edit=True,
            )

    async def load_submission(_db: object, **_kwargs: object) -> QaSubmittedCanary:
        return QaSubmittedCanary(plan_id=operation, task_id=task_id)

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    monkeypatch.setattr(qa_router, "load_qa_canary_submission", load_submission)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = Broker
    application.dependency_overrides[task_service_dep] = object
    body = _issuer_body() | {"execution_name": "job/executions/one"}
    headers = {
        "Authorization": "Bearer oidc",
        "X-Wiki-QA-Capability": "z" * 43,
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        task = await http.post(
            "/internal/document-transform-qa/canary/task", json=body, headers=headers
        )
        result = await http.post(
            "/internal/document-transform-qa/canary/result", json=body, headers=headers
        )
        evidence = await http.post(
            "/internal/document-transform-qa/canary/evidence", json=body, headers=headers
        )

    assert task.status_code == result.status_code == evidence.status_code == 200
    assert operation != task_id
    assert authorization_calls == 10
    assert read_calls == 3
    assert task.json()["task_id"] == result.json()["task_id"] == str(task_id)
    assert result.json()["output"]["file_id"] == "server-derived-output"
    assert result.json()["output"]["web_url"] == (
        "https://docs.google.com/document/d/server-derived-output/edit"
    )
    assert result.json()["applied_patch_count"] == 750
    assert result.json()["short_link_alias"] == "security-review"
    assert result.json()["result_sha256"] == evidence.json()["result_sha256"]
    assert evidence.json()["source_revision_id"] == "source-revision"
    assert len(evidence.json()["findings"]) == 100
    assert evidence.json()["finding_total_count"] == 150
    # The review findings are all beyond the bounded 100-item response. The
    # aggregate keeps omitted findings fail-closed for the workload verifier.
    assert evidence.json()["review_finding_count"] == 50
    assert evidence.json()["findings_truncated"] is True
    assert evidence.json()["findings"][0]["code"] == "finding-000"
    assert evidence.json()["findings"][-1]["code"] == "finding-099"
    assert len(evidence.json()["result_sha256"]) == 64
    permission_evidence = evidence.json()["qa_permission_evidence"]
    assert permission_evidence["status"] == "verified_owner_only"
    assert permission_evidence["operation_id"] == str(task_id)
    assert permission_evidence["source_sha256"] == "c" * 64
    assert permission_evidence["output_revision_id"] == "output-revision"
    assert permission_evidence["destination"]["file_id"] == "destination"
    assert permission_evidence["output"]["file_id"] == "server-derived-output"
    assert "access_scope" not in evidence.text
    for response in (task, result, evidence):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert len(response.content) < 64 * 1024
        assert "error_detail" not in response.text
        assert "pdf" not in response.text.casefold()
        assert "release_sha" not in response.text
        assert "image_digest" not in response.text
        assert "source_folder_id" not in response.text
        assert "destination_folder_id" not in response.text
        assert "user_email" not in response.text
        assert "capability_expires_at" not in response.text

    Broker.forged_operation = True
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        forged_result = await http.post(
            "/internal/document-transform-qa/canary/result", json=body, headers=headers
        )
        forged_evidence = await http.post(
            "/internal/document-transform-qa/canary/evidence", json=body, headers=headers
        )

    assert authorization_calls == 14
    assert read_calls == 5
    for response in (forged_result, forged_evidence):
        assert response.status_code == 409
        assert response.json() == {"detail": "QA canary state is unavailable"}
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert "77777777" not in response.text

    Broker.forged_operation = False
    for forged_resource_id in (None, FileId("different-output")):
        Broker.task_resource_id = forged_resource_id
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://api.test"
        ) as http:
            forged_result = await http.post(
                "/internal/document-transform-qa/canary/result", json=body, headers=headers
            )
            forged_evidence = await http.post(
                "/internal/document-transform-qa/canary/evidence", json=body, headers=headers
            )
        for response in (forged_result, forged_evidence):
            assert response.status_code == 409
            assert response.json() == {"detail": "QA canary state is unavailable"}
            assert "different-output" not in response.text


@pytest.mark.parametrize("response_kind", ["task", "result"])
def test_task_and_result_response_total_byte_guards(response_kind: str) -> None:
    now = datetime.now(UTC)
    task_response = qa_router.QaCanaryTaskResponse(
        task_id=TaskId(uuid.UUID("66666666-6666-4666-8666-666666666666")),
        task_status=TaskStatus.SUCCEEDED,
        attempt_count=1,
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    response = (
        task_response
        if response_kind == "task"
        else qa_router.QaCanaryResultResponse(
            **task_response.model_dump(),
            operation_id=task_response.task_id,
            result_sha256="d" * 64,
            output=qa_router.QaCanaryResultOutput(
                file_id=FileId("output"),
                web_url="https://docs.google.com/document/d/output/edit",
            ),
            changed=True,
            needs_review=False,
            applied_patch_count=750,
            deterministic_patch_count=500,
            model_patch_count=250,
            promoted_heading_count=20,
            short_link_alias=None,
            short_link_status=None,
        )
    )
    with pytest.raises(HTTPException, match="QA canary state is unavailable"):
        qa_router._bounded_response(response, 1)


def test_evidence_response_total_byte_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    result = _transform_result(finding_count=0)
    task = _succeeded_task(result, now=now)
    monkeypatch.setattr(qa_router, "_MAX_QA_EVIDENCE_RESPONSE_BYTES", 1)
    with pytest.raises(HTTPException, match="QA canary state is unavailable"):
        qa_router._evidence_response(
            task,
            result,
            permission_evidence=_permission_evidence(),
            result_sha256="d" * 64,
        )


def test_stored_result_hash_total_byte_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _transform_result(finding_count=0)
    monkeypatch.setattr(qa_router, "QA_MAX_STORED_RESULT_BYTES", 1)
    with pytest.raises(HTTPException, match="QA canary state is unavailable"):
        qa_router._result_sha256(result)


@pytest.mark.parametrize(
    "result",
    [
        _transform_result(finding_count=qa_router.QA_MAX_STORED_FINDINGS + 1),
        _transform_result(finding_count=0).model_copy(
            update={"pipeline_version": "x" * (128 * 1024 + 1)}
        ),
        _transform_result(finding_count=0).model_copy(
            update={
                "model_ids": [
                    f"model-{index}" for index in range(qa_router.QA_MAX_STORED_MODEL_IDS + 1)
                ]
            }
        ),
        _transform_result(finding_count=0).model_copy(
            update={
                "tool_versions": {
                    f"tool-{index}": "1"
                    for index in range(qa_router.QA_MAX_STORED_TOOL_VERSIONS + 1)
                }
            }
        ),
    ],
    ids=("findings", "utf8-metadata", "model-ids", "tool-versions"),
)
def test_succeeded_result_rejects_oversized_retained_metadata(
    result: DocumentTransformResult,
) -> None:
    now = datetime.now(UTC)
    task_id = TaskId(uuid.UUID("66666666-6666-4666-8666-666666666666"))
    result = result.model_copy(update={"operation_id": task_id})
    task = _succeeded_task(result, now=now, task_id=task_id)
    authority = _result_authority(now)

    with pytest.raises(HTTPException, match="QA canary state is unavailable"):
        qa_router._require_succeeded_transform(task, authority=authority)


@pytest.mark.parametrize("revision_field", ["source_revision_id", "output_revision_id"])
def test_result_requires_exact_source_and_output_revisions(revision_field: str) -> None:
    now = datetime.now(UTC)
    task_id = TaskId(uuid.UUID("66666666-6666-4666-8666-666666666666"))
    result = _transform_result(operation_id=task_id).model_copy(update={revision_field: None})
    task = _succeeded_task(result, now=now, task_id=task_id)
    authority = _result_authority(now)

    with pytest.raises(HTTPException, match="QA canary state is unavailable"):
        qa_router._require_succeeded_transform(task, authority=authority)


def test_result_requires_exact_owner_only_permission_evidence_bindings() -> None:
    now = datetime.now(UTC)
    task_id = TaskId(uuid.UUID("66666666-6666-4666-8666-666666666666"))
    valid = _transform_result(operation_id=task_id)
    evidence = _permission_evidence(operation_id=task_id)
    task = _succeeded_task(valid, now=now, task_id=task_id)
    authority = _result_authority(now)
    qa_router._require_succeeded_transform(task, authority=authority)
    mismatches = [
        evidence.model_copy(
            update={"operation_id": uuid.UUID("77777777-7777-4777-8777-777777777777")}
        ),
        evidence.model_copy(update={"source_sha256": "f" * 64}),
        evidence.model_copy(update={"output_revision_id": DocsRevisionId("different-revision")}),
        evidence.model_copy(
            update={
                "destination": evidence.destination.model_copy(
                    update={"file_id": FileId("different-destination")}
                )
            }
        ),
        evidence.model_copy(
            update={
                "output": evidence.output.model_copy(update={"file_id": FileId("different-output")})
            }
        ),
        evidence.model_copy(
            update={
                "output": evidence.output.model_copy(
                    update={"parent_folder_id": FileId("different-destination")}
                )
            }
        ),
    ]

    for mismatch in mismatches:
        with pytest.raises(HTTPException, match="QA canary state is unavailable"):
            qa_router._require_permission_evidence_binding(valid, mismatch, authority=authority)


async def test_canary_reads_reject_caller_selected_task_and_file_before_authorization(
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
        pytest.fail("caller-selected identity reached capability authorization")

    monkeypatch.setattr(qa_router, "authorize_qa_capability", authorize)
    db = _Db()
    application = _application(db, _user())
    application.dependency_overrides[qa_router.qa_canary_broker_dep] = object
    application.dependency_overrides[task_service_dep] = object
    body = _issuer_body() | {
        "execution_name": "job/executions/one",
        "plan_id": "55555555-5555-4555-8555-555555555555",
        "task_id": "66666666-6666-4666-8666-666666666666",
        "source_file_id": "attacker-selected-doc",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://api.test"
    ) as http:
        response = await http.post(
            "/internal/document-transform-qa/canary/task",
            json=body,
            headers={
                "Authorization": "Bearer oidc",
                "X-Wiki-QA-Capability": "z" * 43,
            },
        )

    assert response.status_code == 422
    assert "attacker-selected-doc" not in response.text
    assert "66666666" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert db.commits == db.rollbacks == 0
