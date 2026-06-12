from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Tolerate stale keys lingering in a developer's .env.
        extra="ignore",
    )

    # Database — exactly one connection path must be configured:
    # * DATABASE_URL: direct asyncpg DSN (local dev / CI, password auth)
    # * DB_INSTANCE_CONNECTION_NAME + DB_IAM_USER: Cloud SQL Python
    #   Connector with IAM database authentication (production; no
    #   password exists anywhere)
    DATABASE_URL: str | None = None
    DB_INSTANCE_CONNECTION_NAME: str | None = None
    DB_IAM_USER: str | None = None
    DB_NAME: str = "sarj_wiki"
    SECRET_KEY: str
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/callback"
    FERNET_KEY: str
    ALLOWED_DOMAIN: str = "sarj.ai"
    FRONTEND_URL: str = "http://localhost:3000"
    # Cookie Domain for the auth JWT pair. None = host-only (dev); prod
    # sets "sarj.ai" so the Next server receives the JWTs on document loads.
    COOKIE_DOMAIN: str | None = None
    LOG_JSON: bool = False
    LOG_LEVEL: str = "INFO"

    @model_validator(mode="after")
    def exactly_one_database_path(self) -> Settings:
        dsn = self.DATABASE_URL is not None
        connector = self.DB_INSTANCE_CONNECTION_NAME is not None or self.DB_IAM_USER is not None
        if dsn == connector:
            msg = (
                "Configure exactly one database path: DATABASE_URL,"
                + " or DB_INSTANCE_CONNECTION_NAME + DB_IAM_USER"
            )
            raise ValueError(msg)
        if connector and (self.DB_INSTANCE_CONNECTION_NAME is None or self.DB_IAM_USER is None):
            raise ValueError("DB_INSTANCE_CONNECTION_NAME and DB_IAM_USER must be set together")
        return self

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_min_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    @field_validator("FERNET_KEY")
    @classmethod
    def fernet_key_valid(cls, v: str) -> str:
        from cryptography.fernet import Fernet

        try:
            Fernet(v.encode())
        except Exception as e:
            raise ValueError(
                "FERNET_KEY must be a valid Fernet key (32 url-safe base64-encoded bytes)"
            ) from e
        return v


settings = Settings()  # type: ignore[call-arg]  # ty: ignore[missing-argument]
