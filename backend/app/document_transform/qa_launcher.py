from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
import hmac
from typing import Annotated, Protocol, runtime_checkable
import uuid

import google.auth
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
import httpx
from pydantic import UUID4, BaseModel, ConfigDict, Field, ValidationError


_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_RUN_API = "https://run.googleapis.com/v2"
_MAX_INSPECTION_ATTEMPTS = 10
_MAX_INSPECTION_DELAY = timedelta(seconds=2)


class QaLaunchError(Exception):
    pass


class AccessTokenProvider(Protocol):
    async def token(self) -> str: ...


@runtime_checkable
class _RefreshableCredentials(Protocol):
    token: str | None

    def refresh(self, request: GoogleAuthRequest) -> None: ...


def _refresh_access_token(credentials: _RefreshableCredentials) -> str:
    credentials.refresh(GoogleAuthRequest())
    token = credentials.token
    if not isinstance(token, str) or not token:
        raise QaLaunchError
    return token


class ApplicationDefaultAccessTokenProvider:
    async def token(self) -> str:
        return await asyncio.to_thread(self._refresh)

    @staticmethod
    def _refresh() -> str:
        credentials, _ = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
        if not isinstance(credentials, _RefreshableCredentials):
            raise QaLaunchError
        return _refresh_access_token(credentials)


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _OperationMetadata(_ApiModel):
    name: str


class _RunOperation(_ApiModel):
    name: str
    metadata: _OperationMetadata


class _Environment(_ApiModel):
    name: str
    value: str


class _Container(_ApiModel):
    image: str
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    env: list[_Environment] = Field(default_factory=list)


class _TaskTemplate(_ApiModel):
    service_account: str = Field(alias="serviceAccount")
    max_retries: int = Field(alias="maxRetries")
    timeout: str
    containers: list[_Container]


class _Execution(_ApiModel):
    name: str
    uid: UUID4
    creator: str
    job: str
    parallelism: Annotated[int, Field(ge=1, le=1)]
    task_count: Annotated[int, Field(alias="taskCount", ge=1, le=1)]
    template: _TaskTemplate


@dataclass(frozen=True, slots=True)
class QaExecutionAttestation:
    execution_name: str
    execution_uid: uuid.UUID
    job_name: str
    image: str
    release_sha: str


class CloudRunQaLauncher:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        token_provider: AccessTokenProvider,
        job_name: str,
        image: str,
        release_sha: str,
        worker_service_account: str,
        backend_service_account: str,
        project: str,
        bootstrap_url: str,
        inspection_attempts: int = 5,
        inspection_delay: timedelta = timedelta(milliseconds=250),
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 1 <= inspection_attempts <= _MAX_INSPECTION_ATTEMPTS:
            raise QaLaunchError
        if not timedelta(0) <= inspection_delay <= _MAX_INSPECTION_DELAY:
            raise QaLaunchError
        self._http = http
        self._token_provider = token_provider
        self._job_name = job_name
        self._image = image
        self._release_sha = release_sha
        self._worker_service_account = worker_service_account.casefold()
        self._backend_service_account = backend_service_account.casefold()
        self._project = project
        self._bootstrap_url = bootstrap_url
        self._inspection_attempts = inspection_attempts
        self._inspection_delay = inspection_delay
        self._sleeper = sleeper

    async def launch(self) -> QaExecutionAttestation:
        try:
            token = await self._token_provider.token()
            headers = {"Authorization": f"Bearer {token}"}
            operation = await self._start(headers)
            self._validate_operation(operation)
            execution = await self._inspect(operation.metadata.name, headers)
        except (
            GoogleAuthError,
            QaLaunchError,
            httpx.HTTPError,
            ValueError,
            ValidationError,
        ) as exc:
            raise QaLaunchError from exc
        return self._attest(execution)

    async def attest_execution(self, execution_name: str) -> QaExecutionAttestation:
        if not execution_name.startswith(f"{self._job_name}/executions/"):
            raise QaLaunchError
        try:
            token = await self._token_provider.token()
            execution = await self._inspect(execution_name, {"Authorization": f"Bearer {token}"})
        except (
            GoogleAuthError,
            QaLaunchError,
            httpx.HTTPError,
            ValueError,
            ValidationError,
        ) as exc:
            raise QaLaunchError from exc
        return self._attest(execution)

    async def _start(self, headers: dict[str, str]) -> _RunOperation:
        launched = await self._http.post(
            f"{_RUN_API}/{self._job_name}:run",
            headers=headers,
            json={},
            timeout=15.0,
        )
        launched.raise_for_status()
        return _RunOperation.model_validate(launched.json())

    async def _inspect(self, execution_name: str, headers: dict[str, str]) -> _Execution:
        for attempt in range(self._inspection_attempts):
            inspected = await self._http.get(
                f"{_RUN_API}/{execution_name}",
                headers=headers,
                timeout=15.0,
            )
            if inspected.status_code != httpx.codes.NOT_FOUND:
                inspected.raise_for_status()
                return _Execution.model_validate(inspected.json())
            if attempt + 1 < self._inspection_attempts:
                await self._sleeper(self._inspection_delay.total_seconds())
        raise QaLaunchError

    def _validate_operation(self, operation: _RunOperation) -> None:
        operation_prefix = self._job_name.replace("/jobs/", "/operations/").rsplit("/", 1)[0]
        execution_prefix = f"{self._job_name}/executions/"
        if not operation.name.startswith(
            f"{operation_prefix}/"
        ) or not operation.metadata.name.startswith(execution_prefix):
            raise QaLaunchError

    def _attest(self, execution: _Execution) -> QaExecutionAttestation:
        trusted_template = all(
            (
                hmac.compare_digest(execution.job, self._job_name),
                hmac.compare_digest(execution.creator.casefold(), self._backend_service_account),
                hmac.compare_digest(
                    execution.template.service_account.casefold(), self._worker_service_account
                ),
                execution.template.max_retries == 0,
                execution.template.timeout == "7200s",
                len(execution.template.containers) == 1,
            )
        )
        if not trusted_template:
            raise QaLaunchError
        container = execution.template.containers[0]
        env = self._environment(container.env)
        expected = {
            "GOOGLE_CLOUD_PROJECT": self._project,
            "WIKI_QA_DURABLE_ARTIFACT_ROOT": "/var/lib/wiki-qa",
            "WIKI_QA_BOOTSTRAP_URL": self._bootstrap_url,
            "WIKI_QA_AUDIENCE": self._bootstrap_url.removesuffix("/bootstrap") + "/exchange",
            "WIKI_QA_IMAGE_DIGEST": self._image.rsplit("@", 1)[1],
            "RELEASE_SHA": self._release_sha,
        }
        trusted_execution = all(
            (
                hmac.compare_digest(execution.name, execution.name.strip()),
                execution.name.startswith(f"{self._job_name}/executions/"),
                hmac.compare_digest(container.image, self._image),
                not container.command,
                not container.args,
                env.keys() == expected.keys(),
                all(
                    hmac.compare_digest(env.get(name, ""), value)
                    for name, value in expected.items()
                ),
            )
        )
        if not trusted_execution:
            raise QaLaunchError
        return QaExecutionAttestation(
            execution_name=execution.name,
            execution_uid=execution.uid,
            job_name=execution.job,
            image=container.image,
            release_sha=env["RELEASE_SHA"],
        )

    @staticmethod
    def _environment(values: list[_Environment]) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in values:
            if item.name in result:
                raise QaLaunchError
            result[item.name] = item.value
        return result
