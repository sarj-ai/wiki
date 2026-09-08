from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from app.document_transform.qa_contract import (
    PRODUCTION_QA_BOOTSTRAP_URL,
    PRODUCTION_QA_CANARY_PLAN_URL,
    PRODUCTION_QA_CANARY_SUBMIT_URL,
    PRODUCTION_QA_EXCHANGE_URL,
)
from scripts import qa_document_transform_cloud as worker


_EXECUTION = (
    "projects/test-project/locations/me-central2/jobs/"
    "sarj-wiki-document-transform-qa/executions/execution-one"
)
_RELEASE = "a" * 40
_DIGEST = "sha256:" + "b" * 64


def _runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELEASE_SHA", _RELEASE)
    monkeypatch.setenv("WIKI_QA_IMAGE_DIGEST", _DIGEST)
    monkeypatch.setenv("WIKI_QA_AUDIENCE", PRODUCTION_QA_EXCHANGE_URL)
    monkeypatch.setenv("WIKI_QA_BOOTSTRAP_URL", PRODUCTION_QA_BOOTSTRAP_URL)
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", _EXECUTION)


def _bootstrap() -> dict[str, object]:
    return {
        "capability_token": "c" * 43,
        "execution_name": _EXECUTION,
        "release_sha": _RELEASE,
        "image_digest": _DIGEST,
        "source_folder_id": "source",
        "destination_folder_id": "destination",
        "mode": "both",
        "expires_at": "2026-09-07T00:05:00Z",
        "job_expires_at": "2026-09-07T02:00:00Z",
        "user_email": "operator@sarj.ai",
    }


async def test_worker_bootstraps_without_transport_ids_then_submits_brokered_canary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime(monkeypatch)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="oidc-token")
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            body = json.loads(request.content)
            assert set(body) == {"execution_name", "request_nonce"}
            assert body["execution_name"] == _EXECUTION
            return httpx.Response(200, json=_bootstrap())
        if str(request.url) == PRODUCTION_QA_CANARY_PLAN_URL:
            return httpx.Response(200, json={"plan_id": "plan-one"})
        return httpx.Response(202, json={"task_status": "pending"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await worker.run(http)

    assert len(requests) == 4
    assert requests[0].headers["metadata-flavor"] == "Google"
    assert all("c" * 43 not in str(request.url) for request in requests)
    assert requests[-1].headers["x-wiki-qa-capability"] == "c" * 43


def test_cloud_entrypoint_reports_submission_but_never_marks_it_complete(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def submitted_without_terminal_evidence() -> None:
        return None

    monkeypatch.setattr(worker, "_main", submitted_without_terminal_evidence)
    monkeypatch.setattr(sys, "argv", ["qa_document_transform_cloud"])

    with pytest.raises(SystemExit, match="3"):
        worker.main()

    assert json.loads(capsys.readouterr().out) == {"status": "submitted"}


async def test_worker_retries_only_narrow_unbound_conflict_before_any_transform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime(monkeypatch)
    bootstrap_calls = 0

    async def no_wait(_seconds: float) -> None:
        return None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bootstrap_calls
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="oidc-token")
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            bootstrap_calls += 1
            if bootstrap_calls == 1:
                return httpx.Response(409, json={"detail": "QA execution is not bound yet"})
            return httpx.Response(200, json=_bootstrap())
        if str(request.url) == PRODUCTION_QA_CANARY_PLAN_URL:
            return httpx.Response(200, json={"plan_id": "plan-one"})
        return httpx.Response(202, json={"task_status": "pending"})

    monkeypatch.setattr(worker.asyncio, "sleep", no_wait)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await worker.run(http)
    assert bootstrap_calls == 2


async def test_worker_does_not_retry_forbidden_or_submit_without_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime(monkeypatch)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="oidc-token")
        return httpx.Response(403, json={"detail": "QA credential is invalid"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await worker.run(http)
    assert calls == 2


async def test_worker_rejects_spoofed_execution_binding_before_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _runtime(monkeypatch)
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="oidc-token")
        hostile = _bootstrap() | {"execution_name": _EXECUTION.replace("execution-one", "other")}
        return httpx.Response(200, json=hostile)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(worker.QaWorkerError):
            await worker.run(http)

    assert PRODUCTION_QA_CANARY_PLAN_URL not in urls
    assert PRODUCTION_QA_CANARY_SUBMIT_URL not in urls


@pytest.mark.parametrize("task_status", ["pending", "running", "succeeded"])
async def test_worker_accepts_idempotent_submission_success_states(
    monkeypatch: pytest.MonkeyPatch, task_status: str
) -> None:
    _runtime(monkeypatch)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="oidc-token")
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            return httpx.Response(200, json=_bootstrap())
        if str(request.url) == PRODUCTION_QA_CANARY_PLAN_URL:
            return httpx.Response(200, json={"plan_id": "plan-one"})
        return httpx.Response(202, json={"task_status": task_status})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await worker.run(http)


@pytest.mark.parametrize("task_status", ["failed", "cancelled"])
async def test_worker_rejects_terminal_failure_submission_states(
    monkeypatch: pytest.MonkeyPatch, task_status: str
) -> None:
    _runtime(monkeypatch)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="oidc-token")
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            return httpx.Response(200, json=_bootstrap())
        if str(request.url) == PRODUCTION_QA_CANARY_PLAN_URL:
            return httpx.Response(200, json={"plan_id": "plan-one"})
        return httpx.Response(202, json={"task_status": task_status})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(worker.ValidationError):
            await worker.run(http)


@pytest.mark.parametrize("status_code", [200, 201, 204])
async def test_worker_requires_submit_accepted_status_before_parsing(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _runtime(monkeypatch)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "metadata.google.internal":
            return httpx.Response(200, text="oidc-token")
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            return httpx.Response(200, json=_bootstrap())
        if str(request.url) == PRODUCTION_QA_CANARY_PLAN_URL:
            return httpx.Response(200, json={"plan_id": "plan-one"})
        return httpx.Response(status_code, json={"task_status": "pending"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(worker.QaWorkerError):
            await worker.run(http)


def test_cloud_entrypoint_rejects_all_runtime_arguments_without_echoing_values() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.qa_document_transform_cloud", "--run-id=secret"],
        cwd=Path(__file__).parents[1],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert '"error": "runtime_overrides_forbidden"' in result.stderr
    assert "secret" not in result.stderr
