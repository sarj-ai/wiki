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
    WEBHOOK_BASE_URL: str = ""  # e.g. "https://yourdomain.com" — empty disables webhook registration


settings = Settings()  # type: ignore[call-arg]
