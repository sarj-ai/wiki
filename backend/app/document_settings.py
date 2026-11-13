from typing import Self, override

from pydantic import HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.database_config import NonEmptyStr


# Admit model IDs only after checking Google's Pro-class capability and support
# for thinking_level=HIGH. Configuration must never downgrade to Flash.
_HIGH_THINKING_DOCUMENT_MODELS = frozenset({"gemini-3.1-pro-preview", "gemini-4-argon"})


class DocumentSettings(BaseSettings):
    """Document transformation settings resolved through the parent sources."""

    model_config = SettingsConfigDict(extra="ignore")

    TRANSFORM_UNSLOP_MODEL: NonEmptyStr = "gemini-3.1-pro-preview"
    TRANSFORM_FALLBACK_MODEL: NonEmptyStr = "gemini-3.1-pro-preview"
    TRANSFORM_RESTYLE_MODEL: NonEmptyStr = "gemini-3.1-pro-preview"
    TRANSFORM_LOGO_URI: HttpUrl = HttpUrl("https://wiki.sarj.ai/brand/sarj-logo-light.png")
    TRANSFORM_IN_PLACE_ENABLED: bool = False
    TRANSFORM_QUEUE: NonEmptyStr | None = None
    TRANSFORM_TARGET_URL: NonEmptyStr | None = None
    TRANSFORM_UPLOAD_BUCKET: NonEmptyStr | None = None
    TRANSFORM_UPLOAD_SIGNER: NonEmptyStr | None = None

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The parent owns environment, dotenv and secrets precedence, including
        # explicit _env_file=None. Do not read ambient sources a second time.
        sources = super().settings_customise_sources(
            settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        )
        return tuple(source for source in sources if source is init_settings)

    @model_validator(mode="after")
    def transform_config_is_complete(self) -> Self:
        if (self.TRANSFORM_QUEUE is None) != (self.TRANSFORM_TARGET_URL is None):
            msg = "Document Transform queue settings must be configured together"
            raise ValueError(msg)
        return self

    @field_validator(
        "TRANSFORM_UNSLOP_MODEL",
        "TRANSFORM_FALLBACK_MODEL",
        "TRANSFORM_RESTYLE_MODEL",
    )
    @classmethod
    def document_transform_models_are_exact(cls, value: str) -> str:
        if value == "latest" or value.endswith("-latest"):
            msg = "Document Transform requires an exact Gemini model ID"
            raise ValueError(msg)
        if value not in _HIGH_THINKING_DOCUMENT_MODELS:
            msg = "Document Transform requires a verified Pro-class model with HIGH thinking"
            raise ValueError(msg)
        return value
