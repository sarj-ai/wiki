from datetime import timedelta
from typing import Annotated, Literal, Self

from cryptography.fernet import Fernet
from pydantic import (
    Field,
    HttpUrl,
    PostgresDsn,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
DomainStr = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"),
]
InstanceConnectionName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*:[a-z0-9-]+:[a-z][a-z0-9-]*$")
]
LogLevel = Literal["TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"]


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: PostgresDsn | None = None
    DB_INSTANCE_CONNECTION_NAME: InstanceConnectionName | None = None
    DB_IAM_USER: NonEmptyStr | None = None
    DB_NAME: NonEmptyStr = "sarj_wiki"
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
    SIDEBAR_CACHE_TTL: Annotated[timedelta, Field(gt=timedelta(0))] = timedelta(minutes=10)

    @property
    def frontend_origin(self) -> str:
        return str(self.FRONTEND_URL).rstrip("/")

    @model_validator(mode="after")
    def exactly_one_database_path(self) -> Self:
        dsn = self.DATABASE_URL is not None
        connector = self.DB_INSTANCE_CONNECTION_NAME is not None or self.DB_IAM_USER is not None
        if dsn == connector:
            msg = (
                "Configure exactly one database path: DATABASE_URL,"
                " or DB_INSTANCE_CONNECTION_NAME + DB_IAM_USER"
            )
            raise ValueError(msg)
        if connector and (self.DB_INSTANCE_CONNECTION_NAME is None or self.DB_IAM_USER is None):
            msg_0 = "DB_INSTANCE_CONNECTION_NAME and DB_IAM_USER must be set together"
            raise ValueError(msg_0)
        return self

    @field_validator("FERNET_KEY", "CACHE_FERNET_KEY")
    @classmethod
    def fernet_keys_valid(cls, v: SecretStr | None) -> SecretStr | None:
        if v is None:
            return None
        if not parse_fernet_keys(v.get_secret_value()):
            msg = "must be a comma-separated list of valid Fernet keys"
            raise ValueError(msg)
        return v


settings = Settings()  # type: ignore[call-arg]
