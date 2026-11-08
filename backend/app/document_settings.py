from typing import Annotated, Self, override

from pydantic import Field, HttpUrl, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.database_config import NonEmptyStr


class DocumentSettings(BaseSettings):
    """Document transformation settings resolved through the parent sources."""

    model_config = SettingsConfigDict(extra="ignore")

    TRANSFORM_UNSLOP_MODEL: NonEmptyStr = "gemini-3.8-flash"
    TRANSFORM_FALLBACK_MODEL: NonEmptyStr = "gemini-3.7-flash"
    TRANSFORM_RESTYLE_MODEL: NonEmptyStr = "gemini-4-argon"
    TRANSFORM_LOGO_URI: HttpUrl = HttpUrl("https://wiki.sarj.ai/brand/sarj-logo-light.png")
    TRANSFORM_IN_PLACE_ENABLED: bool = False
    TRANSFORM_QUEUE: NonEmptyStr | None = None
    TRANSFORM_TARGET_URL: NonEmptyStr | None = None
    TRANSFORM_UPLOAD_BUCKET: NonEmptyStr | None = None
    TRANSFORM_UPLOAD_SIGNER: NonEmptyStr | None = None
    TRANSFORM_QA_AUDIENCE: NonEmptyStr | None = None
    TRANSFORM_QA_BOOTSTRAP_URL: NonEmptyStr | None = None
    TRANSFORM_QA_SERVICE_ACCOUNT: NonEmptyStr | None = None
    TRANSFORM_QA_IMAGE_DIGEST: Annotated[str | None, Field(pattern=r"^sha256:[0-9a-f]{64}$")] = None
    TRANSFORM_QA_JOB: Annotated[
        str | None,
        Field(
            pattern=r"^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/locations/[a-z0-9-]+/jobs/[a-z][a-z0-9-]{0,61}[a-z0-9]$"
        ),
    ] = None
    TRANSFORM_QA_IMAGE: Annotated[
        str | None,
        Field(
            pattern=r"^[a-z0-9-]+-docker[.]pkg[.]dev/[a-z0-9-]+/[a-z0-9-]+/[a-z0-9-]+@sha256:[0-9a-f]{64}$"
        ),
    ] = None
    TRANSFORM_QA_RELEASE_SHA: Annotated[str | None, Field(pattern=r"^[0-9a-f]{40}$")] = None
    TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT: NonEmptyStr | None = None
    TRANSFORM_QA_CORPUS_SOURCE_FOLDER_ID: NonEmptyStr | None = None
    TRANSFORM_QA_OPERATOR_EMAILS: tuple[str, ...] = ()

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
        self._validate_qa_config()
        return self

    def _validate_qa_config(self) -> None:
        qa_base_config = (
            self.TRANSFORM_QA_AUDIENCE,
            self.TRANSFORM_QA_SERVICE_ACCOUNT,
            self.TRANSFORM_QA_IMAGE_DIGEST,
        )
        qa_launcher_config = (
            self.TRANSFORM_QA_BOOTSTRAP_URL,
            self.TRANSFORM_QA_JOB,
            self.TRANSFORM_QA_IMAGE,
            self.TRANSFORM_QA_RELEASE_SHA,
            self.TRANSFORM_QA_BACKEND_SERVICE_ACCOUNT,
        )
        base_configured = any(value is not None for value in qa_base_config)
        launcher_configured = any(value is not None for value in qa_launcher_config) or bool(
            self.TRANSFORM_QA_OPERATOR_EMAILS
        )
        if base_configured and not all(value is not None for value in qa_base_config):
            msg = "Document Transform QA legacy base settings must be configured together"
            raise ValueError(msg)
        if launcher_configured and (
            not all(value is not None for value in qa_base_config)
            or not all(value is not None for value in qa_launcher_config)
            or not self.TRANSFORM_QA_OPERATOR_EMAILS
        ):
            msg = "Document Transform QA launcher settings and operator allowlist must be complete"
            raise ValueError(msg)
        if (
            self.TRANSFORM_QA_IMAGE is not None
            and self.TRANSFORM_QA_IMAGE_DIGEST is not None
            and not self.TRANSFORM_QA_IMAGE.endswith(f"@{self.TRANSFORM_QA_IMAGE_DIGEST}")
        ):
            msg = "Document Transform QA image and digest must identify the same immutable image"
            raise ValueError(msg)

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
        return value

    @field_validator("TRANSFORM_QA_OPERATOR_EMAILS")
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
