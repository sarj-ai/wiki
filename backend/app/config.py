from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str
    SECRET_KEY: str
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/callback"
    SERVICE_ACCOUNT_KEY_PATH: str = "service-account-key.json"
    DRIVE_ROOT_FOLDER_ID: str
    FERNET_KEY: str
    ALLOWED_DOMAIN: str = "sarj.ai"
    FRONTEND_URL: str = "http://localhost:3000"
    WEBHOOK_BASE_URL: str = (
        ""  # e.g. "https://yourdomain.com" — empty disables webhook registration
    )
    ADMIN_IMPERSONATE_EMAIL: str = ""  # Workspace admin email for Admin SDK impersonation
    CLOUD_SCHEDULER_SA_EMAIL: str = ""  # Cloud Scheduler service account for OIDC verification
    INTERNAL_SCHEDULER_SECRET: str = ""  # Shared secret fallback for local dev
    INTERNAL_AUDIENCE: str = (
        ""  # Cloud Run service URL used as OIDC audience for internal endpoints
    )
    SHARED_DRIVE_ID: str = (
        ""  # Shared Drive ID for incremental Changes API — only needed if SA has drive-level access
    )
    DRIVE_WEBHOOK_TOKEN: str = ""  # Token sent with Drive webhook registrations for verification

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_min_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    @field_validator("INTERNAL_SCHEDULER_SECRET")
    @classmethod
    def scheduler_secret_min_length(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("INTERNAL_SCHEDULER_SECRET must be at least 32 characters when set")
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


settings = Settings()  # type: ignore[call-arg]
