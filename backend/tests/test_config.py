from __future__ import annotations

from datetime import timedelta

from cryptography.fernet import Fernet
from pydantic import ValidationError
import pytest

from app.config import Settings, load_environment_settings
from app.database_config import DatabaseSettings


_BASELINE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "DB_INSTANCE_CONNECTION_NAME": None,
    "DB_IAM_USER": None,
    "SECRET_KEY": "x" * 32,
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "FERNET_KEY": Fernet.generate_key().decode(),
    "CACHE_FERNET_KEY": None,
    "GOOGLE_REDIRECT_URI": None,
    "ALLOWED_DOMAIN": None,
    "FRONTEND_URL": None,
    "COOKIE_DOMAIN": None,
    "LOG_JSON": None,
    "LOG_LEVEL": None,
    "API_DOCS_ENABLED": None,
    "RELEASE_SHA": None,
    "SIDEBAR_CACHE_TTL": None,
    "CLOUD_TASKS_QUEUE": None,
    "CLOUD_TASKS_LOCATION": None,
    "CLOUD_TASKS_TARGET_URL": None,
    "CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT": None,
    "DOCUMENT_TRANSFORM_QUEUE": None,
    "DOCUMENT_TRANSFORM_TARGET_URL": None,
    "DOCUMENT_TRANSFORM_QA_AUDIENCE": None,
    "DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL": None,
    "DOCUMENT_TRANSFORM_QA_JOB": None,
    "DOCUMENT_TRANSFORM_QA_IMAGE": None,
    "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST": None,
    "DOCUMENT_TRANSFORM_QA_RELEASE_SHA": None,
    "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT": None,
    "DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT": None,
    "DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS": None,
}


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> Settings:
    for key, value in {**_BASELINE, **env}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return load_environment_settings(Settings, load_dotenv=False)


def test_baseline_parses_with_typed_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch)
    assert str(s.DATABASE_URL) == "postgresql+asyncpg://u:p@localhost:5432/db"
    assert s.SECRET_KEY.get_secret_value() == "x" * 32
    assert s.LOG_LEVEL == "INFO"
    assert s.RELEASE_SHA == "dev"
    assert timedelta(minutes=10) == s.SIDEBAR_CACHE_TTL


def test_secrets_do_not_leak_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch)
    rendered = repr(s)
    assert "client-secret" not in rendered
    assert s.FERNET_KEY.get_secret_value() not in rendered
    assert "x" * 32 not in rendered


def test_cache_fernet_rotation_keys_are_validated_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    newest, oldest = Fernet.generate_key().decode(), Fernet.generate_key().decode()

    settings = _settings(monkeypatch, CACHE_FERNET_KEY=f"{newest},{oldest}")

    assert settings.CACHE_FERNET_KEY is not None
    assert settings.CACHE_FERNET_KEY.get_secret_value() == f"{newest},{oldest}"
    assert newest not in repr(settings)
    assert oldest not in repr(settings)


@pytest.mark.parametrize(
    "duplicate",
    [True, False],
)
def test_cache_fernet_rotation_ring_is_bounded_and_unique(
    monkeypatch: pytest.MonkeyPatch,
    duplicate: bool,
) -> None:
    key = Fernet.generate_key().decode()
    ring = (
        f"{key},{key}" if duplicate else ",".join(Fernet.generate_key().decode() for _ in range(4))
    )

    with pytest.raises(ValidationError):
        _settings(monkeypatch, CACHE_FERNET_KEY=ring)


def test_frontend_origin_strips_httpurl_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, FRONTEND_URL="https://wiki.sarj.ai")
    assert str(s.FRONTEND_URL) == "https://wiki.sarj.ai/"
    assert s.frontend_origin == "https://wiki.sarj.ai"


def test_cookie_domain_must_cover_https_frontend_and_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch,
        COOKIE_DOMAIN="sarj.ai",
        FRONTEND_URL="https://wiki.sarj.ai",
        GOOGLE_REDIRECT_URI="https://api-wiki.sarj.ai/auth/callback",
    )
    assert settings.COOKIE_DOMAIN == "sarj.ai"

    with pytest.raises(ValidationError, match="requires HTTPS"):
        _settings(
            monkeypatch,
            COOKIE_DOMAIN="sarj.ai",
            FRONTEND_URL="http://wiki.sarj.ai",
            GOOGLE_REDIRECT_URI="https://api-wiki.sarj.ai/auth/callback",
        )

    with pytest.raises(ValidationError, match="must contain both"):
        _settings(
            monkeypatch,
            COOKIE_DOMAIN="sarj.ai",
            FRONTEND_URL="https://wiki.example.com",
            GOOGLE_REDIRECT_URI="https://api-wiki.sarj.ai/auth/callback",
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_URL", "mysql://u:p@localhost/db"),
        ("SECRET_KEY", "x" * 31),
        ("GOOGLE_CLIENT_ID", ""),
        ("GOOGLE_CLIENT_SECRET", ""),
        ("FERNET_KEY", "not-a-fernet-key"),
        ("CACHE_FERNET_KEY", "not-a-fernet-key"),
        ("GOOGLE_REDIRECT_URI", "not-a-url"),
        ("FRONTEND_URL", "wiki.sarj.ai"),
        ("ALLOWED_DOMAIN", "Sarj.AI"),
        ("COOKIE_DOMAIN", "localhost"),
        ("LOG_LEVEL", "VERBOSE"),
        ("RELEASE_SHA", "ABCDEF" * 6 + "ABCD"),
        ("RELEASE_SHA", "abc123"),
        ("SIDEBAR_CACHE_TTL", "PT0S"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _settings(monkeypatch, **{key: value})


def test_sidebar_cache_ttl_parses_iso8601(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, SIDEBAR_CACHE_TTL="PT5M")
    assert timedelta(minutes=5) == s.SIDEBAR_CACHE_TTL


def test_release_sha_accepts_full_lowercase_git_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    release_sha = "a" * 40
    assert release_sha == _settings(monkeypatch, RELEASE_SHA=release_sha).RELEASE_SHA


def test_old_qa_environment_remains_a_valid_legacy_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    legacy = {
        "DOCUMENT_TRANSFORM_QA_AUDIENCE": (
            "https://api.example.test/internal/document-transform-qa/exchange"
        ),
        "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST": digest,
        "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT": "qa@test-project.iam.gserviceaccount.com",
    }

    configured = _settings(monkeypatch, **legacy)

    assert digest == configured.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST
    assert configured.DOCUMENT_TRANSFORM_QA_JOB is None
    assert configured.DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS == ()


@pytest.mark.parametrize(
    "missing",
    [
        "DOCUMENT_TRANSFORM_QA_AUDIENCE",
        "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST",
        "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT",
    ],
)
def test_qa_legacy_profile_rejects_partial_base_configuration(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    legacy = {
        "DOCUMENT_TRANSFORM_QA_AUDIENCE": (
            "https://api.example.test/internal/document-transform-qa/exchange"
        ),
        "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT": "qa@test-project.iam.gserviceaccount.com",
    }

    with pytest.raises(ValidationError, match="legacy base settings must be configured together"):
        _settings(monkeypatch, **(legacy | {missing: None}))


def test_qa_launcher_profile_is_complete_and_digest_consistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    qa = {
        "DOCUMENT_TRANSFORM_QA_AUDIENCE": (
            "https://api.example.test/internal/document-transform-qa/exchange"
        ),
        "DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL": (
            "https://api.example.test/internal/document-transform-qa/bootstrap"
        ),
        "DOCUMENT_TRANSFORM_QA_JOB": (
            "projects/test-project/locations/me-central2/jobs/sarj-wiki-document-transform-qa"
        ),
        "DOCUMENT_TRANSFORM_QA_IMAGE": (
            "me-central2-docker.pkg.dev/test-project/repository/runner@" + digest
        ),
        "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST": digest,
        "DOCUMENT_TRANSFORM_QA_RELEASE_SHA": "b" * 40,
        "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT": "qa@test-project.iam.gserviceaccount.com",
        "DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT": (
            "backend@test-project.iam.gserviceaccount.com"
        ),
        "DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS": '["operator@sarj.ai"]',
    }
    configured = _settings(monkeypatch, **qa)
    assert digest == configured.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST
    assert configured.DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS == ("operator@sarj.ai",)

    with pytest.raises(ValidationError, match="must identify the same immutable image"):
        _settings(
            monkeypatch,
            **(qa | {"DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST": "sha256:" + "c" * 64}),
        )


@pytest.mark.parametrize(
    "missing",
    [
        "DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL",
        "DOCUMENT_TRANSFORM_QA_JOB",
        "DOCUMENT_TRANSFORM_QA_IMAGE",
        "DOCUMENT_TRANSFORM_QA_RELEASE_SHA",
        "DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT",
        "DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS",
    ],
)
def test_qa_launcher_profile_rejects_partial_or_mixed_configuration(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    digest = "sha256:" + "a" * 64
    launcher = {
        "DOCUMENT_TRANSFORM_QA_AUDIENCE": (
            "https://api.example.test/internal/document-transform-qa/exchange"
        ),
        "DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL": (
            "https://api.example.test/internal/document-transform-qa/bootstrap"
        ),
        "DOCUMENT_TRANSFORM_QA_JOB": (
            "projects/test-project/locations/me-central2/jobs/sarj-wiki-document-transform-qa"
        ),
        "DOCUMENT_TRANSFORM_QA_IMAGE": (
            "me-central2-docker.pkg.dev/test-project/repository/runner@" + digest
        ),
        "DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST": digest,
        "DOCUMENT_TRANSFORM_QA_RELEASE_SHA": "b" * 40,
        "DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT": "qa@test-project.iam.gserviceaccount.com",
        "DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT": (
            "backend@test-project.iam.gserviceaccount.com"
        ),
        "DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS": '["operator@sarj.ai"]',
    }

    with pytest.raises(ValidationError, match="launcher settings and operator allowlist"):
        _settings(monkeypatch, **(launcher | {missing: None}))


def test_database_settings_do_not_load_application_secrets() -> None:
    database_fields = {
        "DATABASE_URL",
        "DB_IAM_USER",
        "DB_INSTANCE_CONNECTION_NAME",
        "DB_NAME",
    }
    assert set(DatabaseSettings.model_fields) == database_fields


def test_db_paths_are_mutually_exclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="exactly one database path"):
        _settings(
            monkeypatch,
            DB_INSTANCE_CONNECTION_NAME="sarj-docs:me-central1:wiki",
            DB_IAM_USER="svc@sarj-docs.iam",
        )
    with pytest.raises(ValidationError, match="exactly one database path"):
        _settings(monkeypatch, DATABASE_URL=None)


def test_connector_path_requires_both_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        _settings(
            monkeypatch,
            DATABASE_URL=None,
            DB_INSTANCE_CONNECTION_NAME="sarj-docs:me-central1:wiki",
        )


def test_instance_connection_name_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(
        monkeypatch,
        DATABASE_URL=None,
        DB_INSTANCE_CONNECTION_NAME="sarj-docs:me-central1:wiki",
        DB_IAM_USER="svc@sarj-docs.iam",
    )
    assert s.DB_INSTANCE_CONNECTION_NAME == "sarj-docs:me-central1:wiki"
    with pytest.raises(ValidationError):
        _settings(
            monkeypatch,
            DATABASE_URL=None,
            DB_INSTANCE_CONNECTION_NAME="missing-the-colons",
            DB_IAM_USER="svc@sarj-docs.iam",
        )


def test_cloud_tasks_settings_are_all_or_none(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="must be configured together"):
        _settings(monkeypatch, CLOUD_TASKS_QUEUE="smart-create")

    settings = _settings(
        monkeypatch,
        CLOUD_TASKS_QUEUE="smart-create",
        CLOUD_TASKS_LOCATION="us-central1",
        CLOUD_TASKS_TARGET_URL="https://wiki-api.sarj.ai/internal/tasks/smart-create",
        CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT=("wiki-task-invoker@sarj-docs.iam.gserviceaccount.com"),
    )

    assert settings.CLOUD_TASKS_QUEUE == "smart-create"
