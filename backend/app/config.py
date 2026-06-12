from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore legacy keys from the pre-pivot stack
        # (SERVICE_ACCOUNT_KEY_PATH, DRIVE_ROOT_FOLDER_ID,
        # ADMIN_IMPERSONATE_EMAIL, etc.) that may linger in a
        # developer's .env until they clean it up post-cutover.
        extra="ignore",
    )

    DATABASE_URL: str
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
