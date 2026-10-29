from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hmac
import json
import re
import sys
import time
from typing import Any, Literal, Self
from urllib.parse import quote
import uuid

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import typer

from app.document_transform.qa_contract import (
    PRODUCTION_QA_BOOTSTRAP_URL,
    PRODUCTION_QA_CANARY_EVIDENCE_URL,
    PRODUCTION_QA_CANARY_PLAN_URL,
    PRODUCTION_QA_CANARY_RESULT_URL,
    PRODUCTION_QA_CANARY_SUBMIT_URL,
    PRODUCTION_QA_CANARY_TASK_URL,
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
_MAX_TASK_RESPONSE_BYTES = 4 * 1024
_MAX_RESULT_RESPONSE_BYTES = 8 * 1024
_MAX_EVIDENCE_RESPONSE_BYTES = 64 * 1024
_MAX_TERMINAL_WAIT = timedelta(minutes=90)
_POLL_INTERVAL = timedelta(seconds=2)
_CAPABILITY_REFRESH_MARGIN = timedelta(minutes=2)
_REQUEST_TIMEOUT = timedelta(seconds=30)
_METADATA_REQUEST_TIMEOUT = timedelta(seconds=5)
_ATTESTATION_CLOCK_SKEW = timedelta(seconds=60)
_TRANSIENT_ATTEMPTS = 3
_TRANSIENT_STATUS_CODES = frozenset({429, 502, 503, 504})
_MIME_DOC = "application/vnd.google-apps.document"
_MIME_FOLDER = "application/vnd.google-apps.folder"
CLI = typer.Typer(add_completion=False, add_help_option=False, pretty_exceptions_enable=False)


class QaWorkerError(Exception):
    pass


class _RuntimeBinding(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    release_sha: str = Field(default="", validation_alias="RELEASE_SHA")
    image_digest: str = Field(default="", validation_alias="WIKI_QA_IMAGE_DIGEST")
    audience: str = Field(default="", validation_alias="WIKI_QA_AUDIENCE")
    bootstrap_url: str = Field(default="", validation_alias="WIKI_QA_BOOTSTRAP_URL")
    execution_name: str = Field(default="", validation_alias="CLOUD_RUN_EXECUTION")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Bootstrap(_StrictModel):
    capability_token: str = Field(min_length=32, max_length=256)
    execution_name: str = Field(min_length=1, max_length=255)
    release_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_folder_id: str = Field(min_length=1, max_length=255)
    destination_folder_id: str = Field(min_length=1, max_length=255)
    mode: Literal["format", "unslop", "both"]
    expires_at: AwareDatetime
    job_expires_at: AwareDatetime
    user_email: str = Field(min_length=1, max_length=320)


class _Capability(_StrictModel):
    capability_token: str = Field(min_length=32, max_length=256)
    expires_at: AwareDatetime
    job_expires_at: AwareDatetime
    user_email: str = Field(min_length=1, max_length=320)


@dataclass(frozen=True, slots=True)
class _RenewedCapability:
    oidc_token: str
    capability: _Capability


class _SessionBinding(_StrictModel):
    authorized: Literal[True]
    capability_expires_at: AwareDatetime
    job_expires_at: AwareDatetime
    user_email: str = Field(min_length=1, max_length=320)
    release_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_folder_id: str = Field(min_length=1, max_length=255)
    destination_folder_id: str = Field(min_length=1, max_length=255)
    mode: Literal["format", "unslop", "both"]


class _Plan(_SessionBinding):
    plan_id: uuid.UUID
    idempotency_key: uuid.UUID
    source_file_id: str = Field(min_length=1, max_length=255)
    source_name: str = Field(min_length=1, max_length=255)
    source_modified_time: str = Field(min_length=1, max_length=128)
    destination_version: str = Field(pattern=r"^[0-9]+$", max_length=64)
    destination_permission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_strategy: Literal["copy"]


class _Submission(_SessionBinding):
    plan_id: uuid.UUID
    task_id: uuid.UUID
    task_status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    replayed: bool
    output_strategy: Literal["copy"]


class _Task(_StrictModel):
    task_id: uuid.UUID
    task_status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    attempt_count: int = Field(ge=0)
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        match self.task_status:
            case "pending":
                valid = (
                    self.attempt_count == 0 and self.started_at is None and self.finished_at is None
                )
            case "running":
                valid = (
                    self.attempt_count > 0
                    and self.started_at is not None
                    and self.finished_at is None
                )
            case "cancelled":
                valid = (
                    self.attempt_count == 0
                    and self.started_at is None
                    and self.finished_at is not None
                )
            case _:
                valid = (
                    self.attempt_count > 0
                    and self.started_at is not None
                    and self.finished_at is not None
                )
        chronology_valid = (
            (self.started_at is None or self.created_at <= self.started_at)
            and (self.finished_at is None or self.created_at <= self.finished_at)
            and (
                self.started_at is None
                or self.finished_at is None
                or self.started_at <= self.finished_at
            )
        )
        if not valid or not chronology_valid:
            message = "invalid task lifecycle"
            raise ValueError(message)
        return self


class _ResultOutput(_StrictModel):
    file_id: str = Field(min_length=1, max_length=255)
    web_url: str = Field(min_length=1, max_length=2_048)


class _Result(_Task):
    operation_id: uuid.UUID
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output: _ResultOutput
    output_strategy: Literal["copy"]
    changed: bool
    needs_review: bool
    applied_patch_count: int = Field(ge=0)
    deterministic_patch_count: int = Field(ge=0)
    model_patch_count: int = Field(ge=0)
    promoted_heading_count: int = Field(ge=0, le=20)
    short_link_alias: str | None = Field(default=None, max_length=63)
    short_link_status: Literal["ready", "unavailable"] | None


class _Validation(_StrictModel):
    source_unchanged: bool
    protected_content_preserved: bool
    structure_preserved: bool
    unsafe_patch_count: int = Field(ge=0)


class _Finding(_StrictModel):
    code: str = Field(min_length=1, max_length=128)
    block_id: str | None = Field(default=None, max_length=255)
    detail: str = Field(max_length=512)
    severity: Literal["informational", "review"]


class _FilePermission(_StrictModel):
    file_id: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=255)
    parent_folder_id: str | None = Field(default=None, max_length=255)
    version: str = Field(pattern=r"^[0-9]+$", max_length=64)
    permission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: AwareDatetime


class _PermissionEvidence(_StrictModel):
    status: Literal["verified_owner_only"]
    operation_id: uuid.UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_revision_id: str = Field(min_length=1, max_length=255)
    destination: _FilePermission
    output: _FilePermission


class _Evidence(_Task):
    operation_id: uuid.UUID
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision_id: str = Field(min_length=1, max_length=255)
    output_revision_id: str = Field(min_length=1, max_length=255)
    validation: _Validation
    findings: list[_Finding] = Field(max_length=100)
    finding_total_count: int = Field(ge=0)
    review_finding_count: int = Field(ge=0)
    findings_truncated: bool
    artifact_kind: Literal["document"]
    qa_permission_evidence: _PermissionEvidence


async def run(http: httpx.AsyncClient) -> None:
    started_at = time.monotonic()
    runtime = _RuntimeBinding()
    _validate_runtime(runtime)
    oidc_token = await _identity_token(http, runtime.audience)
    bootstrap = await _bootstrap(http, runtime, oidc_token)
    job_seconds_remaining = (bootstrap.job_expires_at - datetime.now(UTC)).total_seconds()
    if job_seconds_remaining <= 0:
        raise QaWorkerError
    deadline = started_at + min(_MAX_TERMINAL_WAIT.total_seconds(), job_seconds_remaining)
    binding = {
        "execution_name": bootstrap.execution_name,
        "release_sha": bootstrap.release_sha,
        "image_digest": bootstrap.image_digest,
        "source_folder_id": bootstrap.source_folder_id,
        "destination_folder_id": bootstrap.destination_folder_id,
        "mode": bootstrap.mode,
    }
    planned = await _request(
        http,
        "POST",
        PRODUCTION_QA_CANARY_PLAN_URL,
        headers=_headers(oidc_token=oidc_token, capability_token=bootstrap.capability_token),
        json=binding,
        request_timeout=_REQUEST_TIMEOUT,
        deadline=deadline,
    )
    _require_status(planned, httpx.codes.OK)
    plan = _response_model(planned, _Plan, _MAX_RESULT_RESPONSE_BYTES)
    _require_session_binding(plan, binding, bootstrap)
    if plan.plan_id != plan.idempotency_key:
        raise QaWorkerError
    submitted = await _request(
        http,
        "POST",
        PRODUCTION_QA_CANARY_SUBMIT_URL,
        headers=_headers(oidc_token=oidc_token, capability_token=bootstrap.capability_token),
        json=binding | {"plan_id": str(plan.plan_id)},
        request_timeout=_REQUEST_TIMEOUT,
        deadline=deadline,
    )
    _require_status(submitted, httpx.codes.ACCEPTED)
    submission = _response_model(submitted, _Submission, _MAX_RESULT_RESPONSE_BYTES)
    _require_session_binding(submission, binding, bootstrap)
    if submission.plan_id != plan.plan_id or submission.task_status in {"failed", "cancelled"}:
        raise QaWorkerError

    renewed = await _renew(
        http,
        runtime=runtime,
        binding=binding,
        bootstrap=bootstrap,
        capability_token=bootstrap.capability_token,
        deadline=deadline,
    )
    await _await_terminal_evidence(
        http,
        runtime=runtime,
        binding=binding,
        bootstrap=bootstrap,
        plan=plan,
        submission=submission,
        oidc_token=renewed.oidc_token,
        capability=renewed.capability,
        deadline=deadline,
    )


async def _await_terminal_evidence(
    http: httpx.AsyncClient,
    *,
    runtime: _RuntimeBinding,
    binding: dict[str, str],
    bootstrap: _Bootstrap,
    plan: _Plan,
    submission: _Submission,
    oidc_token: str,
    capability: _Capability,
    deadline: float,
) -> None:
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise QaWorkerError
        if _capability_needs_refresh(capability):
            renewed = await _renew(
                http,
                runtime=runtime,
                binding=binding,
                bootstrap=bootstrap,
                capability_token=capability.capability_token,
                deadline=deadline,
            )
            oidc_token = renewed.oidc_token
            capability = renewed.capability
        task_response = await _request(
            http,
            "POST",
            PRODUCTION_QA_CANARY_TASK_URL,
            headers=_headers(oidc_token=oidc_token, capability_token=capability.capability_token),
            json=binding,
            request_timeout=_REQUEST_TIMEOUT,
            deadline=deadline,
        )
        _require_status(task_response, httpx.codes.OK)
        task = _response_model(task_response, _Task, _MAX_TASK_RESPONSE_BYTES)
        if task.task_id != submission.task_id:
            raise QaWorkerError
        if task.task_status in {"failed", "cancelled"}:
            raise QaWorkerError
        if task.task_status == "succeeded":
            renewed = await _renew(
                http,
                runtime=runtime,
                binding=binding,
                bootstrap=bootstrap,
                capability_token=capability.capability_token,
                deadline=deadline,
            )
            oidc_token = renewed.oidc_token
            capability = renewed.capability
            await _require_terminal_evidence(
                http,
                binding=binding,
                plan=plan,
                task=task,
                oidc_token=oidc_token,
                capability_token=capability.capability_token,
                deadline=deadline,
            )
            return
        await asyncio.sleep(
            min(_POLL_INTERVAL.total_seconds(), max(0.0, deadline - time.monotonic()))
        )


async def _require_terminal_evidence(
    http: httpx.AsyncClient,
    *,
    binding: dict[str, str],
    plan: _Plan,
    task: _Task,
    oidc_token: str,
    capability_token: str,
    deadline: float,
) -> None:
    headers = _headers(oidc_token=oidc_token, capability_token=capability_token)
    result_response = await _request(
        http,
        "POST",
        PRODUCTION_QA_CANARY_RESULT_URL,
        headers=headers,
        json=binding,
        request_timeout=_REQUEST_TIMEOUT,
        deadline=deadline,
    )
    _require_status(result_response, httpx.codes.OK)
    result = _response_model(result_response, _Result, _MAX_RESULT_RESPONSE_BYTES)
    evidence_response = await _request(
        http,
        "POST",
        PRODUCTION_QA_CANARY_EVIDENCE_URL,
        headers=headers,
        json=binding,
        request_timeout=_REQUEST_TIMEOUT,
        deadline=deadline,
    )
    _require_status(evidence_response, httpx.codes.OK)
    evidence = _response_model(evidence_response, _Evidence, _MAX_EVIDENCE_RESPONSE_BYTES)
    _require_terminal_binding(
        binding=binding, plan=plan, task=task, result=result, evidence=evidence
    )


async def _renew(
    http: httpx.AsyncClient,
    *,
    runtime: _RuntimeBinding,
    binding: dict[str, str],
    bootstrap: _Bootstrap,
    capability_token: str,
    deadline: float,
) -> _RenewedCapability:
    oidc_token = await _identity_token(http, runtime.audience, deadline=deadline)
    response = await _request(
        http,
        "POST",
        f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew",
        headers={"Authorization": f"Bearer {oidc_token}"},
        json=binding
        | {
            "credential": capability_token,
            "request_nonce": str(uuid.uuid7()),
        },
        request_timeout=_REQUEST_TIMEOUT,
        deadline=deadline,
    )
    _require_status(response, httpx.codes.OK)
    renewed = _response_model(response, _Capability, _MAX_TASK_RESPONSE_BYTES)
    if (
        hmac.compare_digest(renewed.capability_token, capability_token)
        or renewed.job_expires_at != bootstrap.job_expires_at
        or renewed.user_email != bootstrap.user_email
        or renewed.expires_at <= datetime.now(UTC)
        or renewed.expires_at > renewed.job_expires_at
    ):
        raise QaWorkerError
    return _RenewedCapability(oidc_token=oidc_token, capability=renewed)


def _require_terminal_binding(
    *, binding: dict[str, str], plan: _Plan, task: _Task, result: _Result, evidence: _Evidence
) -> None:
    task_fields = _task_fields(task)
    result_task_fields = _task_fields(result)
    evidence_task_fields = _task_fields(evidence)
    permission = evidence.qa_permission_evidence
    task_started_at = task.started_at
    task_finished_at = task.finished_at
    short_link_valid = (
        result.short_link_alias is not None and result.short_link_status == "ready"
    ) or (result.short_link_alias is None and result.short_link_status == "unavailable")
    expected_url = (
        f"https://docs.google.com/document/d/{quote(result.output.file_id, safe='')}/edit"
    )
    if not all(
        (
            task.task_status == "succeeded",
            task_fields == result_task_fields == evidence_task_fields,
            result.operation_id == task.task_id == evidence.operation_id == permission.operation_id,
            result.result_sha256 == evidence.result_sha256,
            result.output.file_id == permission.output.file_id,
            result.output.web_url == expected_url,
            permission.destination.file_id == binding["destination_folder_id"],
            permission.destination.mime_type == _MIME_FOLDER,
            permission.destination.version == plan.destination_version,
            permission.destination.permission_sha256 == plan.destination_permission_sha256,
            permission.output.mime_type == _MIME_DOC,
            permission.output.parent_folder_id == binding["destination_folder_id"],
            permission.source_sha256 == evidence.source_sha256,
            permission.output_revision_id == evidence.output_revision_id,
            result.applied_patch_count
            == result.deterministic_patch_count + result.model_patch_count,
            not result.needs_review,
            short_link_valid,
            evidence.validation.source_unchanged,
            evidence.validation.protected_content_preserved,
            evidence.validation.structure_preserved,
            evidence.validation.unsafe_patch_count == 0,
            evidence.finding_total_count >= len(evidence.findings),
            evidence.review_finding_count == 0,
            evidence.findings_truncated == (evidence.finding_total_count > len(evidence.findings)),
            all(finding.severity == "informational" for finding in evidence.findings),
            task_started_at is not None,
            task_finished_at is not None,
            _observed_during_task(permission.destination.observed_at, task),
            _observed_during_task(permission.output.observed_at, task),
            task_finished_at is not None
            and task_finished_at <= datetime.now(UTC) + _ATTESTATION_CLOCK_SKEW,
        )
    ):
        raise QaWorkerError


def _task_fields(task: _Task) -> tuple[object, ...]:
    return (
        task.task_id,
        task.task_status,
        task.attempt_count,
        task.created_at,
        task.started_at,
        task.finished_at,
    )


def _headers(*, oidc_token: str, capability_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {oidc_token}",
        "X-Wiki-QA-Capability": capability_token,
    }


def _capability_needs_refresh(capability: _Capability) -> bool:
    remaining = (capability.expires_at - datetime.now(UTC)).total_seconds()
    return remaining <= _CAPABILITY_REFRESH_MARGIN.total_seconds()


def _observed_during_task(observed_at: datetime, task: _Task) -> bool:
    if task.started_at is None or task.finished_at is None:
        return False
    return (
        task.started_at - _ATTESTATION_CLOCK_SKEW
        <= observed_at
        <= task.finished_at + _ATTESTATION_CLOCK_SKEW
    )


def _require_session_binding(
    response: _SessionBinding, binding: dict[str, str], bootstrap: _Bootstrap
) -> None:
    response_binding = (
        response.release_sha,
        response.image_digest,
        response.source_folder_id,
        response.destination_folder_id,
        response.mode,
    )
    expected_binding = (
        binding["release_sha"],
        binding["image_digest"],
        binding["source_folder_id"],
        binding["destination_folder_id"],
        binding["mode"],
    )
    if response_binding != expected_binding or (
        response.capability_expires_at != bootstrap.expires_at
        or response.job_expires_at != bootstrap.job_expires_at
        or response.user_email != bootstrap.user_email
    ):
        raise QaWorkerError


async def _request(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    request_timeout: timedelta,
    deadline: float | None = None,
) -> httpx.Response:
    for attempt in range(_TRANSIENT_ATTEMPTS):
        total_timeout = request_timeout.total_seconds()
        if deadline is not None:
            total_timeout = min(total_timeout, deadline - time.monotonic())
        if total_timeout <= 0:
            raise QaWorkerError
        try:
            async with asyncio.timeout(total_timeout):
                response = await http.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                    params=params,
                    timeout=total_timeout,
                )
        except (TimeoutError, httpx.TransportError) as exc:
            if attempt + 1 == _TRANSIENT_ATTEMPTS:
                raise QaWorkerError from exc
        else:
            if response.status_code not in _TRANSIENT_STATUS_CODES:
                return response
            if attempt + 1 == _TRANSIENT_ATTEMPTS:
                return response
        delay = 0.5 * (attempt + 1)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QaWorkerError
            delay = min(delay, remaining)
        await asyncio.sleep(delay)
    raise QaWorkerError  # pragma: no cover


def _require_status(response: httpx.Response, expected: int) -> None:
    if response.status_code != expected:
        raise QaWorkerError


def _response_model[Model: BaseModel](
    response: httpx.Response, model: type[Model], maximum_bytes: int
) -> Model:
    if len(response.content) > maximum_bytes:
        raise QaWorkerError
    try:
        payload = response.json()
    except ValueError as exc:
        raise QaWorkerError from exc
    return model.model_validate(payload)


async def _identity_token(
    http: httpx.AsyncClient, audience: str, *, deadline: float | None = None
) -> str:
    response = await _request(
        http,
        "GET",
        _METADATA_IDENTITY_URL,
        headers={"Metadata-Flavor": "Google"},
        params={"audience": audience, "format": "full"},
        request_timeout=_METADATA_REQUEST_TIMEOUT,
        deadline=deadline,
    )
    _require_status(response, httpx.codes.OK)
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
        response = await _request(
            http,
            "POST",
            runtime.bootstrap_url,
            headers={"Authorization": f"Bearer {oidc_token}"},
            json=body,
            request_timeout=_REQUEST_TIMEOUT,
        )
        if response.status_code == httpx.codes.CONFLICT and attempt + 1 < _BOOTSTRAP_ATTEMPTS:
            await asyncio.sleep(0.5)
            continue
        _require_status(response, httpx.codes.OK)
        result = _response_model(response, _Bootstrap, _MAX_RESULT_RESPONSE_BYTES)
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
    try:
        CLI(standalone_mode=False)
    except typer.TyperException:
        _fail("runtime_overrides_forbidden")


@CLI.command(add_help_option=False)
def _run_command() -> None:
    try:
        asyncio.run(_main())
    except (httpx.HTTPError, ValidationError, QaWorkerError):  # fmt: skip
        _fail("brokered_canary_failed")
    print(json.dumps({"status": "verified"}, sort_keys=True))


async def _main() -> None:
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as http:
        await run(http)


def _fail(code: str) -> None:
    print(json.dumps({"error": code, "status": "failed"}, sort_keys=True), file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
