from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Literal
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.document_transform.qa_contract import (
    PRODUCTION_QA_BOOTSTRAP_URL,
    PRODUCTION_QA_CANARY_PLAN_URL,
    PRODUCTION_QA_CANARY_SUBMIT_URL,
    PRODUCTION_QA_EXCHANGE_URL,
)


_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EXECUTION = re.compile(
    r"^projects/[a-z][a-z0-9-]{4,28}[a-z0-9]/locations/[a-z0-9-]+/jobs/"
    r"[a-z][a-z0-9-]{0,61}[a-z0-9]/executions/[a-z0-9-]+$"
)
_METADATA_IDENTITY_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
)
_BOOTSTRAP_ATTEMPTS = 20
_MAX_ID_TOKEN_LENGTH = 8192
_SUBMITTED_NOT_VERIFIED_EXIT_CODE = 3


class QaWorkerError(Exception):
    """The immutable workload binding or broker response is invalid."""


class _RuntimeBinding(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    release_sha: str = Field(default="", validation_alias="RELEASE_SHA")
    image_digest: str = Field(default="", validation_alias="WIKI_QA_IMAGE_DIGEST")
    audience: str = Field(default="", validation_alias="WIKI_QA_AUDIENCE")
    bootstrap_url: str = Field(default="", validation_alias="WIKI_QA_BOOTSTRAP_URL")
    execution_name: str = Field(default="", validation_alias="CLOUD_RUN_EXECUTION")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ApiResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Bootstrap(_StrictModel):
    capability_token: str = Field(min_length=32, max_length=256)
    execution_name: str
    release_sha: str
    image_digest: str
    source_folder_id: str
    destination_folder_id: str
    mode: Literal["format", "unslop", "both"]
    expires_at: str
    job_expires_at: str
    user_email: str


class _Plan(_ApiResponse):
    plan_id: str


class _Submission(_ApiResponse):
    task_status: Literal["pending", "running", "succeeded"]


async def run(http: httpx.AsyncClient) -> None:
    runtime = _RuntimeBinding()
    _validate_runtime(runtime)
    oidc_token = await _identity_token(http, runtime.audience)
    bootstrap = await _bootstrap(http, runtime, oidc_token)
    binding = {
        "execution_name": bootstrap.execution_name,
        "release_sha": bootstrap.release_sha,
        "image_digest": bootstrap.image_digest,
        "source_folder_id": bootstrap.source_folder_id,
        "destination_folder_id": bootstrap.destination_folder_id,
        "mode": bootstrap.mode,
    }
    headers = {
        "Authorization": f"Bearer {oidc_token}",
        "X-Wiki-QA-Capability": bootstrap.capability_token,
    }
    planned = await http.post(
        PRODUCTION_QA_CANARY_PLAN_URL,
        headers=headers,
        json=binding,
        timeout=30.0,
    )
    planned.raise_for_status()
    plan = _Plan.model_validate(planned.json())
    submitted = await http.post(
        PRODUCTION_QA_CANARY_SUBMIT_URL,
        headers=headers,
        json=binding | {"plan_id": plan.plan_id},
        timeout=30.0,
    )
    if submitted.status_code != httpx.codes.ACCEPTED:
        raise QaWorkerError
    _Submission.model_validate(submitted.json())


async def _identity_token(http: httpx.AsyncClient, audience: str) -> str:
    response = await http.get(
        _METADATA_IDENTITY_URL,
        headers={"Metadata-Flavor": "Google"},
        params={"audience": audience, "format": "full"},
        timeout=5.0,
    )
    response.raise_for_status()
    token = response.text.strip()
    if not token or len(token) > _MAX_ID_TOKEN_LENGTH or not token.isascii():
        raise QaWorkerError
    return token


async def _bootstrap(
    http: httpx.AsyncClient, runtime: _RuntimeBinding, oidc_token: str
) -> _Bootstrap:
    # This value is only a stale/orphan guard: the backend resolves its sole
    # active bound run independently, requires equality, and re-attests it from
    # Cloud Run before issuing a capability.
    body = {
        "execution_name": runtime.execution_name,
        "request_nonce": str(uuid.uuid7()),
    }
    for attempt in range(_BOOTSTRAP_ATTEMPTS):
        response = await http.post(
            runtime.bootstrap_url,
            headers={"Authorization": f"Bearer {oidc_token}"},
            json=body,
            timeout=30.0,
        )
        if response.status_code == httpx.codes.CONFLICT and attempt + 1 < _BOOTSTRAP_ATTEMPTS:
            await asyncio.sleep(0.5)
            continue
        response.raise_for_status()
        result = _Bootstrap.model_validate(response.json())
        if (
            result.execution_name != runtime.execution_name
            or result.release_sha != runtime.release_sha
            or result.image_digest != runtime.image_digest
            or result.source_folder_id == result.destination_folder_id
        ):
            raise QaWorkerError
        return result
    raise QaWorkerError


def _validate_runtime(runtime: _RuntimeBinding) -> None:
    if (
        not _SHA.fullmatch(runtime.release_sha)
        or not _DIGEST.fullmatch(runtime.image_digest)
        or runtime.audience != PRODUCTION_QA_EXCHANGE_URL
        or runtime.bootstrap_url != PRODUCTION_QA_BOOTSTRAP_URL
        or not _EXECUTION.fullmatch(runtime.execution_name)
    ):
        raise QaWorkerError


def main() -> None:
    if len(sys.argv) != 1:
        _fail("runtime_overrides_forbidden")
    try:
        asyncio.run(_main())
    except (httpx.HTTPError, ValidationError, QaWorkerError):  # fmt: skip
        _fail("brokered_canary_failed")
    print(json.dumps({"status": "submitted"}, sort_keys=True))
    # Submission is not QA success. Until a separately reviewed terminal-result
    # and evidence-verification broker exists, make Cloud Run record this
    # execution as incomplete rather than blessing an enqueue response.
    raise SystemExit(_SUBMITTED_NOT_VERIFIED_EXIT_CODE)


async def _main() -> None:
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as http:
        await run(http)


def _fail(code: str) -> None:
    print(json.dumps({"error": code, "status": "failed"}, sort_keys=True), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
