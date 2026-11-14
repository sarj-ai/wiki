from __future__ import annotations

from fastapi import HTTPException
from pydantic import JsonValue
import pytest

from app.config import Settings, settings
from app.tasks import internal_auth
from app.tasks.internal_auth import GoogleOidcTaskRequestAuthenticator


_AUDIENCE = "https://wiki-api.sarj.ai/internal/tasks/smart-create"
_DOCUMENT_TRANSFORM_AUDIENCE = "https://wiki-api.sarj.ai/internal/tasks/document-transform"
_EMAIL = "wiki-task-invoker@sarj-docs.iam.gserviceaccount.com"


def _claims(token: str, audience: str) -> dict[str, JsonValue]:
    del token
    return {
        "iss": "https://accounts.google.com",
        "aud": audience,
        "email": _EMAIL,
        "email_verified": True,
    }


async def test_cloud_task_auth_accepts_exact_verified_service_account() -> None:
    authenticator = GoogleOidcTaskRequestAuthenticator(
        audience=_AUDIENCE,
        service_account_email=_EMAIL,
        verifier=_claims,
    )

    result = await authenticator.authenticate("Bearer signed-token")

    assert result is None


async def test_document_transform_auth_uses_route_specific_audience() -> None:
    seen_audiences: list[str] = []

    def verifier(token: str, audience: str) -> dict[str, JsonValue]:
        seen_audiences.append(audience)
        return _claims(token, audience)

    configured = Settings.model_validate(
        {
            **settings.model_dump(),
            "GOOGLE_CLOUD_PROJECT": "fixture-project",
            "CLOUD_TASKS_QUEUE": "smart-create",
            "CLOUD_TASKS_LOCATION": "us-central1",
            "CLOUD_TASKS_TARGET_URL": _AUDIENCE,
            "CLOUD_TASKS_INVOKER_SERVICE_ACCOUNT": _EMAIL,
            "DOCUMENT": {
                **settings.DOCUMENT.model_dump(),
                "TRANSFORM_QUEUE": "document-transform",
                "TRANSFORM_TARGET_URL": _DOCUMENT_TRANSFORM_AUDIENCE,
            },
        }
    )
    authenticator = internal_auth.document_transform_task_request_authenticator_dep(
        configured, verifier
    )

    await authenticator.authenticate("Bearer signed-token")

    assert seen_audiences == [_DOCUMENT_TRANSFORM_AUDIENCE]


async def test_cloud_task_auth_rejects_missing_bearer_token() -> None:
    authenticator = GoogleOidcTaskRequestAuthenticator(
        audience=_AUDIENCE,
        service_account_email=_EMAIL,
        verifier=_claims,
    )

    with pytest.raises(HTTPException) as caught:
        await authenticator.authenticate(None)

    assert caught.value.status_code == 401


async def test_cloud_task_auth_rejects_other_verified_service_account() -> None:
    def other_claims(token: str, audience: str) -> dict[str, JsonValue]:
        claims = _claims(token, audience)
        claims["email"] = "attacker@sarj-docs.iam.gserviceaccount.com"
        return claims

    authenticator = GoogleOidcTaskRequestAuthenticator(
        audience=_AUDIENCE,
        service_account_email=_EMAIL,
        verifier=other_claims,
    )

    with pytest.raises(HTTPException) as caught:
        await authenticator.authenticate("Bearer signed-token")

    assert caught.value.status_code == 403
