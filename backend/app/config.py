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
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.database_config import DatabaseSettings, NonEmptyStr
from app.mcp.redirect_policy import McpRedirectPolicy


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
    MCP_NATIVE_ENABLED: bool = False
    MCP_NATIVE_ISSUER: HttpUrl | None = None
    MCP_NATIVE_FERNET_KEYS: SecretStr | None = None
    MCP_NATIVE_LOCK_KEY: SecretStr | None = None
    MCP_NATIVE_LOOPBACK_PATHS: tuple[str, ...] = ()
    MCP_NATIVE_HTTPS_CALLBACKS: tuple[str, ...] = ()
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
    DOCUMENT_TRANSFORM_UPLOAD_BUCKET: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_UPLOAD_SIGNER: NonEmptyStr | None = None
    CLOUD_TASKS_QUEUE: NonEmptyStr | None = None
    CLOUD_TASKS_LOCATION: NonEmptyStr | None = None
    CLOUD_TASKS_TARGET_URL: NonEmptyStr | None = None
    CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_QA_AUDIENCE: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST: Annotated[
        str | None, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ] = None
    DOCUMENT_TRANSFORM_QA_JOB: Annotated[
        str | None,
        Field(
            pattern=r"^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/locations/[a-z0-9-]+/jobs/[a-z][a-z0-9-]{0,61}[a-z0-9]$"
        ),
    ] = None
    DOCUMENT_TRANSFORM_QA_IMAGE: Annotated[
        str | None,
        Field(
            pattern=r"^[a-z0-9-]+-docker[.]pkg[.]dev/[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+@sha256:[0-9a-f]{64}$"
        ),
    ] = None
    DOCUMENT_TRANSFORM_QA_RELEASE_SHA: Annotated[str | None, Field(pattern=r"^[0-9a-f]{40}$")] = (
        None
    )
    DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID: NonEmptyStr | None = None
    DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS: tuple[str, ...] = ()

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
        self._validate_qa_config()
        return self

    def _validate_qa_config(self) -> None:
        qa_base_config = (
            self.DOCUMENT_TRANSFORM_QA_AUDIENCE,
            self.DOCUMENT_TRANSFORM_QA_SERVICE_ACCOUNT,
            self.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST,
        )
        qa_launcher_config = (
            self.DOCUMENT_TRANSFORM_QA_BOOTSTRAP_URL,
            self.DOCUMENT_TRANSFORM_QA_JOB,
            self.DOCUMENT_TRANSFORM_QA_IMAGE,
            self.DOCUMENT_TRANSFORM_QA_RELEASE_SHA,
            self.DOCUMENT_TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT,
        )
        base_configured = any(value is not None for value in qa_base_config)
        launcher_configured = any(value is not None for value in qa_launcher_config) or bool(
            self.DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS
        )
        if base_configured and not all(value is not None for value in qa_base_config):
            msg = "Document Transform QA legacy base settings must be configured together"
            raise ValueError(msg)
        if launcher_configured and (
            not all(value is not None for value in qa_base_config)
            or not all(value is not None for value in qa_launcher_config)
            or not self.DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS
        ):
            msg = "Document Transform QA launcher settings and operator allowlist must be complete"
            raise ValueError(msg)
        if (
            self.DOCUMENT_TRANSFORM_QA_IMAGE is not None
            and self.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST is not None
            and not self.DOCUMENT_TRANSFORM_QA_IMAGE.endswith(
                f"@{self.DOCUMENT_TRANSFORM_QA_IMAGE_DIGEST}"
            )
        ):
            msg = "Document Transform QA image and digest must identify the same immutable image"
            raise ValueError(msg)

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

    @field_validator("DOCUMENT_TRANSFORM_QA_OPERATOR_EMAILS")
    @classmethod
    def qa_operator_emails_are_exact(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            not value
            or value != value.casefold()
            or not value.endswith("@sarj.ai")
            or any(character.isspace() for character in value)
            for value in values
        ):
            msg = "Document Transform QA operators must be lowercase sarj.ai email addresses"
            raise ValueError(msg)
        if len(values) != len(set(values)):
            msg = "Document Transform QA operators must be unique"
            raise ValueError(msg)
        return values

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

    @field_validator(
        "FERNET_KEY", "CACHE_FERNET_KEY", "MCP_NATIVE_FERNET_KEYS", "MCP_NATIVE_LOCK_KEY"
    )
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

    @model_validator(mode="after")
    def native_mcp_configuration_is_explicit(self) -> Self:
        McpRedirectPolicy(self.MCP_NATIVE_LOOPBACK_PATHS, self.MCP_NATIVE_HTTPS_CALLBACKS)
        if not self.MCP_NATIVE_ENABLED:
            return self
        issuer = self.MCP_NATIVE_ISSUER
        if (
            issuer is None
            or self.MCP_NATIVE_FERNET_KEYS is None
            or self.MCP_NATIVE_LOCK_KEY is None
        ):
            msg = "Native MCP requires an explicit issuer and independent encryption and lock keys"
            raise ValueError(msg)
        frontend = self.FRONTEND_URL
        callback = self.GOOGLE_REDIRECT_URI
        if (
            not _is_https_origin(issuer)
            or not _is_https_origin(frontend)
            or callback.scheme != "https"
            or issuer.host != callback.host
            or issuer.port != callback.port
        ):
            msg = (
                "Native MCP requires a fixed HTTPS API issuer and same-site trusted frontend origin"
            )
            raise ValueError(msg)
        if self.COOKIE_DOMAIN is None:
            msg = "Native MCP requires an explicit same-site cookie domain"
            raise ValueError(msg)
        keys = parse_fernet_keys(self.MCP_NATIVE_FERNET_KEYS.get_secret_value())
        lock_keys = parse_fernet_keys(self.MCP_NATIVE_LOCK_KEY.get_secret_value())
        existing = parse_fernet_keys(self.FERNET_KEY.get_secret_value())
        if self.CACHE_FERNET_KEY is not None:
            existing.extend(parse_fernet_keys(self.CACHE_FERNET_KEY.get_secret_value()))
        existing.append(self.SECRET_KEY.get_secret_value())
        if (
            len(lock_keys) != 1
            or not set(keys).isdisjoint(lock_keys)
            or not set(keys + lock_keys).isdisjoint(existing)
        ):
            msg = "Native MCP encryption and stable lock keys must be distinct from all Wiki keys"
            raise ValueError(msg)
        if not (self.MCP_NATIVE_LOOPBACK_PATHS or self.MCP_NATIVE_HTTPS_CALLBACKS):
            msg = "Native MCP requires explicitly verified client callback paths"
            raise ValueError(msg)
        if not all(
            (self.GOOGLE_CLOUD_PROJECT, self.CLOUD_TASKS_QUEUE, self.DOCUMENT_TRANSFORM_QUEUE)
        ):
            msg = "Native MCP requires the existing Cloud Tasks and document transformation queues"
            raise ValueError(msg)
        return self


def _is_https_origin(url: HttpUrl) -> bool:
    return (
        url.scheme == "https"
        and url.path == "/"
        and not any(
            value is not None for value in (url.query, url.fragment, url.username, url.password)
        )
    )


def load_environment_settings[SettingsModel: BaseSettings](
    model: type[SettingsModel], *, load_dotenv: bool = True
) -> SettingsModel:
    return model() if load_dotenv else model(_env_file=None)


settings = load_environment_settings(Settings)
