from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable, Mapping
from typing import Annotated, Literal, Protocol, override

from fastapi import Depends, Header, HTTPException, status
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from pydantic import BaseModel, ConfigDict, EmailStr, ValidationError

from app.config import settings


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


class TaskRequestAuthenticator(ABC):
    @abstractmethod
    async def authenticate(self, authorization: str | None) -> None: ...


class GoogleOidcTaskRequestAuthenticator(TaskRequestAuthenticator):
    def __init__(
        self,
        *,
        audience: str,
        service_account_email: str,
        verifier: Callable[[str, str], object] | None = None,
    ) -> None:
        self._audience = audience
        self._service_account_email = service_account_email.casefold()
        self._verifier = verifier or _verify_google_token

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


def _task_request_authenticator(audience: str | None) -> TaskRequestAuthenticator:
    service_account = settings.CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT
    if audience is None or service_account is None:
        msg = "Cloud Tasks OIDC settings are required"
        raise RuntimeError(msg)
    return GoogleOidcTaskRequestAuthenticator(
        audience=audience,
        service_account_email=service_account,
    )


def smart_create_task_request_authenticator_dep() -> TaskRequestAuthenticator:
    return _task_request_authenticator(settings.CLOUD_TASKS_TARGET_URL)


def document_transform_task_request_authenticator_dep() -> TaskRequestAuthenticator:
    return _task_request_authenticator(settings.DOCUMENT_TRANSFORM_TARGET_URL)


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


def _verify_google_token(token: str, audience: str) -> object:
    verifier: _GoogleTokenVerifier = id_token.verify_oauth2_token
    return verifier(token, GoogleAuthRequest(), audience)
