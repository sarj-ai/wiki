from typing import Annotated, Literal, Self, assert_never
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

from app.identifiers import NonEmptyStr
from app.mcp.redirect_policy import McpRedirectPolicy


MAX_FERNET_ROTATION_KEYS = 3


class _Configuration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DisabledCloudTasks(_Configuration):
    kind: Literal["disabled"] = "disabled"


class ConfiguredCloudTasks(_Configuration):
    kind: Literal["configured"] = "configured"
    project: NonEmptyStr
    location: NonEmptyStr
    queue: NonEmptyStr
    target_url: NonEmptyStr
    invoker_service_account: NonEmptyStr

    @field_validator("target_url")
    @classmethod
    def require_internal_target(cls, value: str) -> str:
        if not _is_internal_task_url(value, expected_path="/internal/tasks/smart-create"):
            msg = "Cloud Tasks target must be the HTTPS internal Smart Create endpoint"
            raise ValueError(msg)
        return value


type CloudTasksConfiguration = DisabledCloudTasks | ConfiguredCloudTasks


class DisabledDocumentQueue(_Configuration):
    kind: Literal["disabled"] = "disabled"


class ConfiguredDocumentQueue(_Configuration):
    kind: Literal["configured"] = "configured"
    queue: NonEmptyStr
    target_url: NonEmptyStr

    @field_validator("target_url")
    @classmethod
    def require_internal_target(cls, value: str) -> str:
        if not _is_internal_task_url(value, expected_path="/internal/tasks/document-transform"):
            msg = "Document Transform Cloud Tasks target must be its HTTPS internal endpoint"
            raise ValueError(msg)
        return value


type DocumentQueueConfiguration = DisabledDocumentQueue | ConfiguredDocumentQueue


class DisabledNativeMcp(_Configuration):
    kind: Literal["disabled"] = "disabled"


class EnabledNativeMcp(_Configuration):
    kind: Literal["enabled"] = "enabled"
    issuer: HttpUrl
    fernet_keys: Annotated[
        tuple[SecretStr, ...], Field(min_length=1, max_length=MAX_FERNET_ROTATION_KEYS)
    ]
    lock_key: SecretStr
    redirects: McpRedirectPolicy
    tasks: ConfiguredCloudTasks
    document_tasks: ConfiguredDocumentQueue

    @field_validator("issuer")
    @classmethod
    def require_https_issuer(cls, issuer: HttpUrl) -> HttpUrl:
        if not is_https_origin(issuer):
            msg = "Native MCP requires a fixed HTTPS API issuer"
            raise ValueError(msg)
        return issuer

    @field_validator("fernet_keys")
    @classmethod
    def require_valid_keys(cls, keys: tuple[SecretStr, ...]) -> tuple[SecretStr, ...]:
        for key in keys:
            Fernet(key.get_secret_value().encode())
        return keys

    @field_validator("lock_key")
    @classmethod
    def require_valid_lock_key(cls, key: SecretStr) -> SecretStr:
        Fernet(key.get_secret_value().encode())
        return key

    @model_validator(mode="after")
    def require_independent_keys_and_callbacks(self) -> Self:
        keys = [key.get_secret_value() for key in self.fernet_keys]
        if len(keys) != len(set(keys)) or self.lock_key.get_secret_value() in keys:
            msg = "Native MCP encryption and stable lock keys must be distinct"
            raise ValueError(msg)
        if not (self.redirects.loopback_paths or self.redirects.https_callbacks):
            msg = "Native MCP requires explicitly verified client callback paths"
            raise ValueError(msg)
        return self


type NativeMcpConfiguration = DisabledNativeMcp | EnabledNativeMcp


def is_https_origin(url: HttpUrl) -> bool:
    return (
        url.scheme == "https"
        and url.path == "/"
        and not any(
            value is not None for value in (url.query, url.fragment, url.username, url.password)
        )
    )


def require_cloud_tasks(configuration: CloudTasksConfiguration) -> ConfiguredCloudTasks:
    match configuration:
        case ConfiguredCloudTasks():
            return configuration
        case DisabledCloudTasks():
            msg = "Cloud Tasks settings are required"
            raise RuntimeError(msg)
        case _:
            assert_never(configuration)


def _is_internal_task_url(target_url: str, *, expected_path: str) -> bool:
    parsed = urlsplit(target_url)
    return (
        parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and parsed.scheme == "https"
        and parsed.path == expected_path
        and not parsed.query
        and not parsed.fragment
    )
