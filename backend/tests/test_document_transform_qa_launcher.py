from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
import copy
from datetime import timedelta

import httpx
import pytest

from app.document_transform.qa_launcher import CloudRunQaLauncher, QaLaunchError


_PROJECT = "test-project"
_JOB = f"projects/{_PROJECT}/locations/me-central2/jobs/sarj-wiki-document-transform-qa"
_EXECUTION = f"{_JOB}/executions/execution-one"
_OPERATION = f"projects/{_PROJECT}/locations/me-central2/operations/operation-one"
_IMAGE = (
    "me-central2-docker.pkg.dev/test-project/sarj-docs-dammam/"
    "document-transform-qa@sha256:" + "a" * 64
)
_RELEASE = "b" * 40
_WORKER = "qa@test-project.iam.gserviceaccount.com"
_BACKEND = "backend@test-project.iam.gserviceaccount.com"
_BOOTSTRAP = "https://api.example.test/internal/document-transform-qa/bootstrap"

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


class _TokenProvider:
    async def token(self) -> str:
        return "access-token"


def _execution() -> dict[str, JsonValue]:
    return {
        "name": _EXECUTION,
        "uid": "12345678-1234-4234-8234-123456789abc",
        "creator": _BACKEND,
        "job": _JOB,
        "parallelism": 1,
        "taskCount": 1,
        "template": {
            "serviceAccount": _WORKER,
            "maxRetries": 0,
            "timeout": "7200s",
            "containers": [
                {
                    "image": _IMAGE,
                    "env": [
                        {"name": "GOOGLE_CLOUD_PROJECT", "value": _PROJECT},
                        {
                            "name": "WIKI_QA_DURABLE_ARTIFACT_ROOT",
                            "value": "/var/lib/wiki-qa",
                        },
                        {"name": "WIKI_QA_BOOTSTRAP_URL", "value": _BOOTSTRAP},
                        {
                            "name": "WIKI_QA_AUDIENCE",
                            "value": "https://api.example.test/internal/document-transform-qa/exchange",
                        },
                        {"name": "WIKI_QA_IMAGE_DIGEST", "value": "sha256:" + "a" * 64},
                        {"name": "RELEASE_SHA", "value": _RELEASE},
                    ],
                }
            ],
        },
    }


def _launcher(
    handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
    *,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> CloudRunQaLauncher:
    return CloudRunQaLauncher(
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        token_provider=_TokenProvider(),
        job_name=_JOB,
        image=_IMAGE,
        release_sha=_RELEASE,
        worker_service_account=_WORKER,
        backend_service_account=_BACKEND,
        project=_PROJECT,
        bootstrap_url=_BOOTSTRAP,
        sleeper=sleeper,
    )


def _replace_nested_json(
    document: dict[str, JsonValue], path: tuple[str | int, ...], value: JsonValue
) -> None:
    target: JsonValue = document
    for part in path[:-1]:
        match target, part:
            case dict() as mapping, str() as key:
                target = mapping[key]
            case list() as sequence, int() as index:
                target = sequence[index]
            case _:
                raise TypeError
    final = path[-1]
    match target, final:
        case dict() as mapping, str() as key:
            mapping[key] = value
        case list() as sequence, int() as index:
            sequence[index] = value
        case _:
            raise TypeError


async def test_backend_launches_configured_job_without_overrides_then_attests_execution() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"name": _OPERATION, "metadata": {"name": _EXECUTION}},
            )
        return httpx.Response(200, json=_execution())

    launcher = _launcher(handler)
    attestation = await launcher.launch()

    assert requests[0].url == f"https://run.googleapis.com/v2/{_JOB}:run"
    assert requests[0].content == b"{}"
    assert requests[0].headers["authorization"] == "Bearer access-token"
    assert requests[1].url == f"https://run.googleapis.com/v2/{_EXECUTION}"
    assert attestation.execution_name == _EXECUTION
    assert attestation.job_name == _JOB
    assert attestation.image == _IMAGE
    assert attestation.release_sha == _RELEASE


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("job",), "projects/other/locations/me-central2/jobs/hostile"),
        (("creator",), "human@example.test"),
        (("parallelism",), 2),
        (("taskCount",), 2),
        (("template", "serviceAccount"), "other@example.test"),
        (("template", "maxRetries"), 1),
        (("template", "timeout"), "7201s"),
        (("template", "containers", 0, "image"), "example.test/image:latest"),
        (("template", "containers", 0, "command"), ["/bin/hostile"]),
        (("template", "containers", 0, "args"), ["--run-id=attacker"]),
    ],
    ids=(
        "job",
        "creator",
        "parallelism",
        "task-count",
        "worker-service-account",
        "retries",
        "timeout",
        "image",
        "command",
        "args",
    ),
)
async def test_execution_attestation_rejects_every_mutable_identity_mismatch(
    path: tuple[str | int, ...], value: JsonValue
) -> None:
    execution = copy.deepcopy(_execution())
    _replace_nested_json(execution, path, value)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"name": _OPERATION, "metadata": {"name": _EXECUTION}},
            )
        return httpx.Response(200, json=execution)

    with pytest.raises(QaLaunchError):
        await _launcher(handler).launch()


async def test_attestation_rejects_extra_environment_including_secrets_and_run_ids() -> None:
    execution = _execution()
    template = execution["template"]
    assert isinstance(template, dict)
    containers = template["containers"]
    assert isinstance(containers, list)
    container = containers[0]
    assert isinstance(container, dict)
    env = container["env"]
    assert isinstance(env, list)
    env.append({"name": "RUN_ID", "value": "caller-controlled"})

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"name": _OPERATION, "metadata": {"name": _EXECUTION}},
            )
        return httpx.Response(200, json=execution)

    with pytest.raises(QaLaunchError):
        await _launcher(handler).launch()


async def test_operation_cannot_redirect_attestation_to_another_job() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "name": _OPERATION,
                "metadata": {
                    "name": "projects/other/locations/me-central2/jobs/hostile/executions/one"
                },
            },
        )

    with pytest.raises(QaLaunchError):
        await _launcher(handler).launch()


async def test_execution_readback_polls_bounded_not_found_before_attesting() -> None:
    inspections = 0
    delays: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal inspections
        if request.method == "POST":
            return httpx.Response(200, json={"name": _OPERATION, "metadata": {"name": _EXECUTION}})
        inspections += 1
        if inspections < 3:
            return httpx.Response(404)
        return httpx.Response(200, json=_execution())

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    launcher = _launcher(handler, sleeper=sleeper)
    attestation = await launcher.launch()

    assert attestation.execution_name == _EXECUTION
    assert inspections == 3
    assert delays == [0.25, 0.25]


async def test_execution_readback_timeout_fails_closed_after_exact_bound() -> None:
    inspections = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal inspections
        if request.method == "POST":
            return httpx.Response(200, json={"name": _OPERATION, "metadata": {"name": _EXECUTION}})
        inspections += 1
        return httpx.Response(404)

    async def no_wait(_delay: float) -> None:
        return None

    launcher = _launcher(handler, sleeper=no_wait)
    with pytest.raises(QaLaunchError):
        await launcher.launch()
    assert inspections == 5


@pytest.mark.parametrize(
    ("attempts", "delay"),
    [
        (0, timedelta(0)),
        (11, timedelta(0)),
        (1, timedelta(milliseconds=-100)),
        (1, timedelta(milliseconds=2100)),
    ],
)
def test_execution_readback_bounds_are_validated(attempts: int, delay: timedelta) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError

    with pytest.raises(QaLaunchError):
        CloudRunQaLauncher(
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            token_provider=_TokenProvider(),
            job_name=_JOB,
            image=_IMAGE,
            release_sha=_RELEASE,
            worker_service_account=_WORKER,
            backend_service_account=_BACKEND,
            project=_PROJECT,
            bootstrap_url=_BOOTSTRAP,
            inspection_attempts=attempts,
            inspection_delay=delay,
        )
