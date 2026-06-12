from __future__ import annotations

from datetime import timedelta

from cryptography.fernet import Fernet
from pydantic import ValidationError
import pytest

from app.config import Settings


_BASELINE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "DB_INSTANCE_CONNECTION_NAME": None,
    "DB_IAM_USER": None,
    "SECRET_KEY": "x" * 32,
    "GOOGLE_CLIENT_ID": "client-id",
    "GOOGLE_CLIENT_SECRET": "client-secret",
    "FERNET_KEY": Fernet.generate_key().decode(),
    "GOOGLE_REDIRECT_URI": None,
    "ALLOWED_DOMAIN": None,
    "FRONTEND_URL": None,
    "COOKIE_DOMAIN": None,
    "LOG_JSON": None,
    "LOG_LEVEL": None,
    "SIDEBAR_CACHE_TTL": None,
}


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> Settings:
    for key, value in {**_BASELINE, **env}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]  # ty: ignore[missing-argument]


def test_baseline_parses_with_typed_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch)
    assert str(s.DATABASE_URL) == "postgresql+asyncpg://u:p@localhost:5432/db"
    assert s.SECRET_KEY.get_secret_value() == "x" * 32
    assert s.LOG_LEVEL == "INFO"
    assert timedelta(minutes=10) == s.SIDEBAR_CACHE_TTL


def test_secrets_do_not_leak_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch)
    rendered = repr(s)
    assert "client-secret" not in rendered
    assert s.FERNET_KEY.get_secret_value() not in rendered
    assert "x" * 32 not in rendered


def test_frontend_origin_strips_httpurl_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, FRONTEND_URL="https://wiki.sarj.ai")
    assert str(s.FRONTEND_URL) == "https://wiki.sarj.ai/"
    assert s.frontend_origin == "https://wiki.sarj.ai"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_URL", "mysql://u:p@localhost/db"),
        ("SECRET_KEY", "x" * 31),  # below min_length
        ("GOOGLE_CLIENT_ID", ""),
        ("GOOGLE_CLIENT_SECRET", ""),
        ("FERNET_KEY", "not-a-fernet-key"),
        ("GOOGLE_REDIRECT_URI", "not-a-url"),
        ("FRONTEND_URL", "wiki.sarj.ai"),
        ("ALLOWED_DOMAIN", "Sarj.AI"),
        ("COOKIE_DOMAIN", "localhost"),
        ("LOG_LEVEL", "VERBOSE"),
        ("SIDEBAR_CACHE_TTL", "PT0S"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch, key: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _settings(monkeypatch, **{key: value})


def test_sidebar_cache_ttl_parses_iso8601(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, SIDEBAR_CACHE_TTL="PT5M")
    assert timedelta(minutes=5) == s.SIDEBAR_CACHE_TTL


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
