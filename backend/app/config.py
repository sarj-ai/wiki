from datetime import timedelta
from typing import Annotated, Literal, Self

from cryptography.fernet import Fernet
from pydantic import (
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import SettingsConfigDict

from app.database_config import DatabaseSettings, NonEmptyStr


DomainStr = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"),
]
LogLevel = Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]
ReleaseSha = Annotated[str, StringConstraints(pattern=r"^(?:dev|[0-9a-f]{40})$")]
_MAX_FERNET_ROTATION_KEYS = 3


def parse_fernet_keys(raw: str) -> list[str]:
    keys = [key for raw_key in raw.split(",") if (key := raw_key.strip())]
    if not keys:
        return []
    try:
        for k in keys:
            Fernet(k.encode())
    except ValueError, TypeError:
        return []
    return keys


class Settings(DatabaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    SECRET_KEY: Annotated[SecretStr, Field(min_length=32)]
    GOOGLE_CLIENT_ID: NonEmptyStr
    GOOGLE_CLIENT_SECRET: Annotated[SecretStr, Field(min_length=1)]
    GOOGLE_REDIRECT_URI: HttpUrl = HttpUrl("http://localhost:8000/auth/callback")
    FERNET_KEY: SecretStr
    CACHE_FERNET_KEY: SecretStr | None = None
    ALLOWED_DOMAIN: DomainStr = "sarj.ai"
    FRONTEND_URL: HttpUrl = HttpUrl("http://localhost:3000")
    COOKIE_DOMAIN: DomainStr | None = None
    LOG_JSON: bool = False
    LOG_LEVEL: LogLevel = "INFO"
    MAINTENANCE_MODE: bool = False
    API_DOCS_ENABLED: bool = False
    RELEASE_SHA: ReleaseSha = "dev"
    SIDEBAR_CACHE_TTL: Annotated[timedelta, Field(gt=timedelta(0))] = timedelta(minutes=10)
    GOOGLE_CLOUD_PROJECT: NonEmptyStr | None = None
    GEMINI_LOCATION: NonEmptyStr = "global"
    GEMINI_MODEL: NonEmptyStr = "gemini-3.5-flash-lite"
    GEMINI_REORGANIZATION_MODEL: NonEmptyStr = "gemini-3.5-flash"
    DOCUMENT_TRANSFORM_UNSLOP_MODEL: NonEmptyStr = "gemini-3.1-flash-lite"
    DOCUMENT_TRANSFORM_FALLBACK_MODEL: NonEmptyStr = "gemini-2.5-flash-lite"
    DOCUMENT_TRANSFORM_RESTYLE_MODEL: NonEmptyStr = "gemini-3.8-flash"
    DOCUMENT_TRANSFORM_LOGO_URI: HttpUrl = HttpUrl("https://wiki.sarj.ai/brand/sarj-logo-light.png")
    DOCUMENT_TRANSFORM_IN_PLACE_ENABLED: bool = False
    DOCUMENT_TRANSFORM_QUEUE: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_TARGET_URL: NonEmptyStr | None = None
    CLOUD_TASKS_QUEUE: NonEmptyStr | None = None
    CLOUD_TASKS_LOCATION: NonEmptyStr | None = None
    CLOUD_TASKS_TARGET_URL: NonEmptyStr | None = None
    CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT: NonEmptyStr | None = None

    @property
    def frontend_origin(self) -> str:
        return str(self.FRONTEND_URL).rstrip("/")

    @model_validator(mode="after")
    def cloud_tasks_config_is_complete(self) -> Self:
        task_config = (
            self.CLOUD_TASKS_QUEUE,
            self.CLOUD_TASKS_LOCATION,
            self.CLOUD_TASKS_TARGET_URL,
            self.CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT,
        )
        if any(value is not None for value in task_config) and not all(
            value is not None for value in task_config
        ):
            msg = "Cloud Tasks settings must be configured together"
            raise ValueError(msg)
        transform_config = (
            self.DOCUMENT_TRANSFORM_QUEUE,
            self.DOCUMENT_TRANSFORM_TARGET_URL,
        )
        if any(value is not None for value in transform_config) and not all(
            value is not None for value in transform_config
        ):
            msg = "Document Transform queue settings must be configured together"
            raise ValueError(msg)
        if any(value is not None for value in transform_config) and not all(
            value is not None for value in task_config
        ):
            msg = "Document Transform requires the shared Cloud Tasks settings"
            raise ValueError(msg)
        return self

    @field_validator(
        "DOCUMENT_TRANSFORM_UNSLOP_MODEL",
        "DOCUMENT_TRANSFORM_FALLBACK_MODEL",
        "DOCUMENT_TRANSFORM_RESTYLE_MODEL",
    )
    @classmethod
    def document_transform_models_are_exact(cls, value: str) -> str:
        if value == "latest" or value.endswith("-latest"):
            msg = "Document Transform requires an exact Gemini model ID"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def cookie_domain_matches_application_hosts(self) -> Self:
        if self.COOKIE_DOMAIN is None:
            return self
        frontend_host = self.FRONTEND_URL.host
        redirect_host = self.GOOGLE_REDIRECT_URI.host
        domain = self.COOKIE_DOMAIN
        if self.FRONTEND_URL.scheme != "https" or self.GOOGLE_REDIRECT_URI.scheme != "https":
            msg = "COOKIE_DOMAIN requires HTTPS frontend and OAuth callback URLs"
            raise ValueError(msg)
        if not all(
            host is not None and (host == domain or host.endswith(f".{domain}"))
            for host in (frontend_host, redirect_host)
        ):
            msg = "COOKIE_DOMAIN must contain both the frontend and OAuth callback hosts"
            raise ValueError(msg)
        return self

    @field_validator("FERNET_KEY", "CACHE_FERNET_KEY")
    @classmethod
    def fernet_keys_valid(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return None
        keys = parse_fernet_keys(v.get_secret_value())
        if not keys:
            msg = "must be a comma-separated list of valid Fernet keys"
            raise ValueError(msg)
        if len(keys) != len(set(keys)):
            msg = "must contain unique Fernet keys"
            raise ValueError(msg)
        if len(keys) > _MAX_FERNET_ROTATION_KEYS:
            msg = "must contain at most three Fernet keys"
            raise ValueError(msg)
        return v


settings = Settings()  # type: ignore[call-arg]
