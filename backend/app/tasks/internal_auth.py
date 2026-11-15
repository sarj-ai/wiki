from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable, Mapping
from typing import Annotated, Literal, Protocol, override

from fastapi import Depends, Header, HTTPException, status
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, EmailStr, JsonValue, RootModel, ValidationError

from app.config import Settings, settings_dep
from app.runtime_config import ConfiguredCloudTasks, require_cloud_tasks


class _CloudTaskClaims(BaseModel):
    model_config = ConfigDict(extra="ignore")

    iss: Literal["accounts.google.com", "https://accounts.google.com"]
    aud: str
    email: EmailStr
    email_verified: Literal[True]


class _GoogleTokenVerifier(Protocol):
    def __call__(
        self,
        id_token: str,
        request: GoogleAuthRequest,
        audience: str,
    ) -> Mapping[str, object]: ...


class _TokenClaimsPayload(RootModel[dict[str, JsonValue]]):
    pass


type _TaskTokenVerifier = Callable[[str, str], dict[str, JsonValue]]


class TaskRequestAuthenticator(ABC):
    @abstractmethod
    async def authenticate(self, authorization: str | None) -> None: ...


class GoogleOidcTaskRequestAuthenticator(TaskRequestAuthenticator):
    def __init__(
        self,
        *,
        audience: str,
        service_account_email: str,
        verifier: _TaskTokenVerifier,
    ) -> None:
        self._audience = audience
        self._service_account_email = service_account_email.casefold()
        self._verifier = verifier

    @override
    async def authenticate(self, authorization: str | None) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise _unauthorized()
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise _unauthorized()
        try:
            raw_claims = await asyncio.to_thread(
                self._verifier,
                token,
                self._audience,
            )
            claims = _CloudTaskClaims.model_validate(raw_claims)
        except (GoogleAuthError, ValueError, ValidationError) as exc:
            raise _unauthorized() from exc
        if claims.aud != self._audience or claims.email.casefold() != self._service_account_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cloud Tasks caller is not allowed",
            )


def _task_request_authenticator(
    audience: str, configuration: ConfiguredCloudTasks, verifier: _TaskTokenVerifier
) -> TaskRequestAuthenticator:
    return GoogleOidcTaskRequestAuthenticator(
        audience=audience,
        service_account_email=configuration.invoker_service_account,
        verifier=verifier,
    )


def task_token_verifier_dep() -> _TaskTokenVerifier:
    return _verify_google_token


def smart_create_task_request_authenticator_dep(
    app_settings: Annotated[Settings, Depends(settings_dep)],
    verifier: Annotated[_TaskTokenVerifier, Depends(task_token_verifier_dep)],
) -> TaskRequestAuthenticator:
    configuration = require_cloud_tasks(app_settings.cloud_tasks)
    return _task_request_authenticator(configuration.target_url, configuration, verifier)


def document_transform_task_request_authenticator_dep(
    app_settings: Annotated[Settings, Depends(settings_dep)],
    verifier: Annotated[_TaskTokenVerifier, Depends(task_token_verifier_dep)],
) -> TaskRequestAuthenticator:
    document = app_settings.DOCUMENT.queue
    if document.kind == "disabled":
        msg = "Document Transform Cloud Tasks settings are required"
        raise RuntimeError(msg)
    return _task_request_authenticator(
        document.target_url, require_cloud_tasks(app_settings.cloud_tasks), verifier
    )


async def require_smart_create_cloud_task(
    authenticator: Annotated[
        TaskRequestAuthenticator,
        Depends(smart_create_task_request_authenticator_dep),
    ],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    await authenticator.authenticate(authorization)


async def require_document_transform_cloud_task(
    authenticator: Annotated[
        TaskRequestAuthenticator,
        Depends(document_transform_task_request_authenticator_dep),
    ],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    await authenticator.authenticate(authorization)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid Cloud Tasks authentication is required",
    )


def _verify_google_token(token: str, audience: str) -> dict[str, JsonValue]:
    verifier: _GoogleTokenVerifier = id_token.verify_oauth2_token
    return _TokenClaimsPayload.model_validate(verifier(token, GoogleAuthRequest(), audience)).root
