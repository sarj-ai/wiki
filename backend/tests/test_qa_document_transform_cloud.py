from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import httpx
import pytest

from app.document_transform.qa_contract import (
    PRODUCTION_QA_BOOTSTRAP_URL,
    PRODUCTION_QA_CANARY_EVIDENCE_URL,
    PRODUCTION_QA_CANARY_PLAN_URL,
    PRODUCTION_QA_CANARY_RESULT_URL,
    PRODUCTION_QA_CANARY_SUBMIT_URL,
    PRODUCTION_QA_CANARY_TASK_URL,
    PRODUCTION_QA_EXCHANGE_URL,
)
from app.identifiers import FileId
from scripts import qa_document_transform_cloud as worker


_EXECUTION = (
    "projects/test-project/locations/me-central2/jobs/"
    "sarj-wiki-document-transform-qa/executions/execution-one"
)
_RELEASE = "a" * 40
_DIGEST = "sha256:" + "b" * 64
_PLAN_ID = "55555555-5555-4555-8555-555555555555"
_TASK_ID = "66666666-6666-4666-8666-666666666666"
_NOW = datetime.now(UTC)
_CREATED = (_NOW - timedelta(seconds=3)).isoformat()
_STARTED = (_NOW - timedelta(seconds=2)).isoformat()
_FINISHED = (_NOW - timedelta(seconds=1)).isoformat()
_CAPABILITY_EXPIRES = (_NOW + timedelta(minutes=30)).isoformat()
_JOB_EXPIRES = (_NOW + timedelta(hours=2)).isoformat()


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
        "expires_at": _CAPABILITY_EXPIRES,
        "job_expires_at": _JOB_EXPIRES,
        "user_email": "operator@sarj.ai",
    }


def _session() -> dict[str, object]:
    return {
        "authorized": True,
        "capability_expires_at": _CAPABILITY_EXPIRES,
        "job_expires_at": _JOB_EXPIRES,
        "user_email": "operator@sarj.ai",
        "release_sha": _RELEASE,
        "image_digest": _DIGEST,
        "source_folder_id": "source",
        "destination_folder_id": "destination",
        "mode": "both",
    }


def _plan() -> dict[str, object]:
    return _session() | {
        "plan_id": _PLAN_ID,
        "idempotency_key": _PLAN_ID,
        "source_file_id": "source-document",
        "source_name": "Security Review",
        "source_modified_time": "2099-09-07T00:00:00Z",
        "destination_version": "7",
        "destination_permission_sha256": "d" * 64,
        "output_strategy": "copy",
    }


def _submission(status: str = "pending") -> dict[str, object]:
    return _session() | {
        "plan_id": _PLAN_ID,
        "task_id": _TASK_ID,
        "task_status": status,
        "replayed": False,
        "output_strategy": "copy",
    }


def _task(status: str = "succeeded") -> dict[str, object]:
    match status:
        case "pending":
            return {
                "task_id": _TASK_ID,
                "task_status": status,
                "attempt_count": 0,
                "created_at": _CREATED,
                "started_at": None,
                "finished_at": None,
            }
        case "running":
            return {
                "task_id": _TASK_ID,
                "task_status": status,
                "attempt_count": 1,
                "created_at": _CREATED,
                "started_at": _STARTED,
                "finished_at": None,
            }
        case "cancelled":
            return {
                "task_id": _TASK_ID,
                "task_status": status,
                "attempt_count": 0,
                "created_at": _CREATED,
                "started_at": None,
                "finished_at": _FINISHED,
            }
        case _:
            return {
                "task_id": _TASK_ID,
                "task_status": status,
                "attempt_count": 1,
                "created_at": _CREATED,
                "started_at": _STARTED,
                "finished_at": _FINISHED,
            }


def _result() -> dict[str, object]:
    return _task() | {
        "operation_id": _TASK_ID,
        "result_sha256": "e" * 64,
        "output": {
            "file_id": "output-document",
            "web_url": "https://docs.google.com/document/d/output-document/edit",
        },
        "output_strategy": "copy",
        "changed": True,
        "needs_review": False,
        "applied_patch_count": 3,
        "deterministic_patch_count": 2,
        "model_patch_count": 1,
        "promoted_heading_count": 1,
        "short_link_alias": "security-review",
        "short_link_status": "ready",
    }


def _permission(file_id: FileId, mime_type: str, parent: str | None) -> dict[str, str | None]:
    return {
        "file_id": file_id,
        "mime_type": mime_type,
        "parent_folder_id": parent,
        "version": "7",
        "permission_sha256": "d" * 64,
        "observed_at": _FINISHED,
    }


def _evidence() -> dict[str, object]:
    return _task() | {
        "operation_id": _TASK_ID,
        "result_sha256": "e" * 64,
        "source_sha256": "f" * 64,
        "source_revision_id": "source-revision",
        "output_revision_id": "output-revision",
        "validation": {
            "source_unchanged": True,
            "protected_content_preserved": True,
            "structure_preserved": True,
            "unsafe_patch_count": 0,
        },
        "findings": [],
        "finding_total_count": 0,
        "review_finding_count": 0,
        "findings_truncated": False,
        "artifact_kind": "document",
        "qa_permission_evidence": {
            "status": "verified_owner_only",
            "operation_id": _TASK_ID,
            "source_sha256": "f" * 64,
            "output_revision_id": "output-revision",
            "destination": _permission(
                FileId("destination"), "application/vnd.google-apps.folder", None
            ),
            "output": _permission(
                FileId("output-document"), "application/vnd.google-apps.document", "destination"
            ),
        },
    }


def _renewal(token: str = "r" * 43) -> dict[str, object]:
    return {
        "capability_token": token,
        "expires_at": _CAPABILITY_EXPIRES,
        "job_expires_at": _JOB_EXPIRES,
        "user_email": "operator@sarj.ai",
    }


def _response_for(request: httpx.Request, *, task_status: str = "succeeded") -> httpx.Response:
    url = str(request.url)
    if request.url.host == "metadata.google.internal":
        return httpx.Response(200, text="oidc-token")
    if url == PRODUCTION_QA_BOOTSTRAP_URL:
        return httpx.Response(200, json=_bootstrap())
    if url == PRODUCTION_QA_CANARY_PLAN_URL:
        return httpx.Response(200, json=_plan())
    if url == PRODUCTION_QA_CANARY_SUBMIT_URL:
        return httpx.Response(202, json=_submission())
    if url == f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew":
        credential = json.loads(request.content)["credential"]
        token = "r" * 43 if credential == "c" * 43 else "s" * 43
        return httpx.Response(200, json=_renewal(token))
    if url == PRODUCTION_QA_CANARY_TASK_URL:
        return httpx.Response(200, json=_task(task_status))
    if url == PRODUCTION_QA_CANARY_RESULT_URL:
        return httpx.Response(200, json=_result())
    if url == PRODUCTION_QA_CANARY_EVIDENCE_URL:
        return httpx.Response(200, json=_evidence())
    raise AssertionError(url)


async def _run(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    _runtime(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await worker.run(http)


async def test_worker_reaches_terminal_verified_evidence_with_rotated_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    task_states = iter(("pending", "running", "succeeded"))

    async def no_wait(_seconds: float) -> None:
        return None

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == PRODUCTION_QA_CANARY_TASK_URL:
            return _response_for(request, task_status=next(task_states))
        return _response_for(request)

    monkeypatch.setattr(worker.asyncio, "sleep", no_wait)
    await _run(monkeypatch, handler)

    urls = [str(request.url) for request in requests]
    assert sum(url.startswith(worker._METADATA_IDENTITY_URL) for url in urls) == 3
    assert urls.count(PRODUCTION_QA_CANARY_TASK_URL) == 3
    assert urls[-2:] == [PRODUCTION_QA_CANARY_RESULT_URL, PRODUCTION_QA_CANARY_EVIDENCE_URL]
    exchange = next(
        request
        for request in requests
        if str(request.url) == f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew"
    )
    assert str(exchange.url) == f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew"
    renewal_body = json.loads(exchange.content)
    bootstrap_body = json.loads(requests[1].content)
    assert set(renewal_body) == {
        "credential",
        "destination_folder_id",
        "execution_name",
        "image_digest",
        "mode",
        "release_sha",
        "request_nonce",
        "source_folder_id",
    }
    assert renewal_body["credential"] == "c" * 43
    assert renewal_body["request_nonce"] != bootstrap_body["request_nonce"]
    assert exchange.headers["authorization"] == "Bearer oidc-token"
    assert requests[-1].headers["x-wiki-qa-capability"] == "s" * 43
    assert all("c" * 43 not in str(request.url) for request in requests)


def test_cloud_entrypoint_exits_zero_only_after_terminal_verification(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def verified() -> None:
        return None

    monkeypatch.setattr(worker, "_main", verified)
    monkeypatch.setattr(sys, "argv", ["qa_document_transform_cloud"])

    worker.main()

    assert json.loads(capsys.readouterr().out) == {"status": "verified"}


def test_cloud_entrypoint_bounds_exhausted_request_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def timed_out() -> None:
        raise worker.QaWorkerError from TimeoutError("secret upstream URL")

    monkeypatch.setattr(worker, "_main", timed_out)
    monkeypatch.setattr(sys, "argv", ["qa_document_transform_cloud"])

    with pytest.raises(SystemExit, match="1"):
        worker.main()

    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err) == {"error": "brokered_canary_failed", "status": "failed"}
    assert "secret" not in captured.err


async def test_worker_retries_only_bootstrap_conflict_before_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_calls = 0

    async def no_wait(_seconds: float) -> None:
        return None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bootstrap_calls
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            bootstrap_calls += 1
            if bootstrap_calls == 1:
                return httpx.Response(409)
        return _response_for(request)

    monkeypatch.setattr(worker.asyncio, "sleep", no_wait)
    await _run(monkeypatch, handler)

    assert bootstrap_calls == 2


async def test_worker_does_not_retry_forbidden_bootstrap_or_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bootstrap_calls = 0
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal bootstrap_calls
        urls.append(str(request.url))
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            bootstrap_calls += 1
            return httpx.Response(403)
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)

    assert bootstrap_calls == 1
    assert PRODUCTION_QA_CANARY_PLAN_URL not in urls


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_name", _EXECUTION.replace("execution-one", "other")),
        ("release_sha", "0" * 40),
        ("image_digest", "sha256:" + "0" * 64),
    ],
)
async def test_worker_rejects_spoofed_bootstrap_binding_before_plan(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url) == PRODUCTION_QA_BOOTSTRAP_URL:
            return httpx.Response(200, json=_bootstrap() | {field: value})
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)

    assert PRODUCTION_QA_CANARY_PLAN_URL not in urls


@pytest.mark.parametrize("status_code", [200, 201, 204])
async def test_worker_requires_exact_submit_accepted_status(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url) == PRODUCTION_QA_CANARY_SUBMIT_URL:
            return httpx.Response(status_code, json=_submission())
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)

    assert f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew" not in urls
    assert PRODUCTION_QA_CANARY_TASK_URL not in urls


@pytest.mark.parametrize("task_status", ["failed", "cancelled"])
async def test_worker_rejects_terminal_task_failures(
    monkeypatch: pytest.MonkeyPatch, task_status: str
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return _response_for(request, task_status=task_status)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)


@pytest.mark.parametrize("task_status", ["failed", "cancelled"])
async def test_worker_rejects_terminal_submission_states_before_renewal(
    monkeypatch: pytest.MonkeyPatch, task_status: str
) -> None:
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url) == PRODUCTION_QA_CANARY_SUBMIT_URL:
            return httpx.Response(202, json=_submission(task_status))
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)
    assert f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew" not in urls


async def test_worker_times_out_without_treating_pending_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker, "_MAX_TERMINAL_WAIT", timedelta(0))

    async def handler(request: httpx.Request) -> httpx.Response:
        return _response_for(request, task_status="pending")

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)


@pytest.mark.parametrize(
    ("target", "path", "value"),
    [
        ("result", ("operation_id",), "77777777-7777-4777-8777-777777777777"),
        ("result", ("result_sha256",), "0" * 64),
        ("result", ("output", "file_id"), "other-output"),
        ("result", ("needs_review",), True),
        ("result", ("model_patch_count",), 2),
        ("result", ("short_link_status",), "unavailable"),
        ("evidence", ("output_revision_id",), "other-revision"),
        ("evidence", ("review_finding_count",), 1),
        ("evidence", ("validation", "structure_preserved"), False),
        (
            "evidence",
            ("qa_permission_evidence", "destination", "file_id"),
            "other-destination",
        ),
        (
            "evidence",
            ("qa_permission_evidence", "destination", "permission_sha256"),
            "0" * 64,
        ),
        (
            "evidence",
            ("qa_permission_evidence", "output", "parent_folder_id"),
            "other-destination",
        ),
        (
            "evidence",
            ("qa_permission_evidence", "source_sha256"),
            "0" * 64,
        ),
        (
            "evidence",
            ("qa_permission_evidence", "output", "observed_at"),
            (_NOW + timedelta(hours=1)).isoformat(),
        ),
        (
            "evidence",
            ("qa_permission_evidence", "destination", "observed_at"),
            (_NOW - timedelta(hours=1)).isoformat(),
        ),
    ],
)
async def test_worker_rejects_terminal_cross_binding_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    path: tuple[str, ...],
    value: object,
) -> None:
    result = _result()
    evidence = _evidence()
    payload = result if target == "result" else evidence
    cursor: Any = payload
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value

    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PRODUCTION_QA_CANARY_RESULT_URL:
            return httpx.Response(200, json=result)
        if str(request.url) == PRODUCTION_QA_CANARY_EVIDENCE_URL:
            return httpx.Response(200, json=evidence)
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)


@pytest.mark.parametrize(
    "endpoint",
    [
        PRODUCTION_QA_BOOTSTRAP_URL,
        PRODUCTION_QA_CANARY_PLAN_URL,
        PRODUCTION_QA_CANARY_SUBMIT_URL,
        PRODUCTION_QA_CANARY_TASK_URL,
        PRODUCTION_QA_CANARY_RESULT_URL,
        PRODUCTION_QA_CANARY_EVIDENCE_URL,
    ],
)
async def test_worker_rejects_extra_fields_in_broker_responses(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        response = _response_for(request)
        if str(request.url) != endpoint:
            return response
        payload = response.json()
        payload["unexpected"] = "hostile"
        status = 202 if endpoint == PRODUCTION_QA_CANARY_SUBMIT_URL else 200
        return httpx.Response(status, json=payload)

    with pytest.raises(worker.ValidationError):
        await _run(monkeypatch, handler)


async def test_worker_rejects_oversized_response_before_json_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PRODUCTION_QA_CANARY_EVIDENCE_URL:
            return httpx.Response(200, content=b"{" + b" " * (64 * 1024))
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)


@pytest.mark.parametrize("transient_status", [429, 502, 503, 504])
async def test_worker_retries_only_allowlisted_transient_statuses(
    monkeypatch: pytest.MonkeyPatch, transient_status: int
) -> None:
    task_calls = 0

    async def no_wait(_seconds: float) -> None:
        return None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal task_calls
        if str(request.url) == PRODUCTION_QA_CANARY_TASK_URL:
            task_calls += 1
            if task_calls == 1:
                return httpx.Response(transient_status)
        return _response_for(request)

    monkeypatch.setattr(worker.asyncio, "sleep", no_wait)
    await _run(monkeypatch, handler)
    assert task_calls == 2


async def test_worker_retries_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_calls = 0

    async def no_wait(_seconds: float) -> None:
        return None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal task_calls
        if str(request.url) == PRODUCTION_QA_CANARY_TASK_URL:
            task_calls += 1
            if task_calls == 1:
                message = "unavailable"
                raise httpx.ConnectError(message, request=request)
        return _response_for(request)

    monkeypatch.setattr(worker.asyncio, "sleep", no_wait)
    await _run(monkeypatch, handler)
    assert task_calls == 2


async def test_worker_refreshes_oidc_and_rotates_capability_during_long_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 0.0
    task_calls = 0
    renewal_calls = 0
    requests: list[httpx.Request] = []

    def monotonic() -> float:
        return clock

    async def advance(_seconds: float) -> None:
        nonlocal clock
        clock += 1

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal renewal_calls, task_calls
        requests.append(request)
        if str(request.url) == f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew":
            renewal_calls += 1
            token = ("r", "s", "t")[renewal_calls - 1] * 43
            return httpx.Response(200, json=_renewal() | {"capability_token": token})
        if str(request.url) == PRODUCTION_QA_CANARY_TASK_URL:
            task_calls += 1
            return _response_for(request, task_status="pending" if task_calls == 1 else "succeeded")
        return _response_for(request)

    monkeypatch.setattr(worker.time, "monotonic", monotonic)
    monkeypatch.setattr(worker.asyncio, "sleep", advance)
    refresh_checks = iter((False, True))
    monkeypatch.setattr(
        worker, "_capability_needs_refresh", lambda _capability: next(refresh_checks)
    )
    await _run(monkeypatch, handler)

    assert renewal_calls == 3
    metadata_calls = [
        request for request in requests if request.url.host == "metadata.google.internal"
    ]
    assert len(metadata_calls) == 4
    assert requests[-1].headers["x-wiki-qa-capability"] == "t" * 43


async def test_worker_does_not_retry_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal task_calls
        if str(request.url) == PRODUCTION_QA_CANARY_TASK_URL:
            task_calls += 1
            return httpx.Response(409)
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)
    assert task_calls == 1


async def test_worker_rejects_spoofed_session_binding_before_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url) == PRODUCTION_QA_CANARY_PLAN_URL:
            hostile = _plan() | {"destination_folder_id": "hostile"}
            return httpx.Response(200, json=hostile)
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)
    assert PRODUCTION_QA_CANARY_SUBMIT_URL not in urls


async def test_worker_rejects_plan_idempotency_mismatch_before_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url) == PRODUCTION_QA_CANARY_PLAN_URL:
            return httpx.Response(
                200,
                json=_plan() | {"idempotency_key": "77777777-7777-4777-8777-777777777777"},
            )
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)
    assert PRODUCTION_QA_CANARY_SUBMIT_URL not in urls


def test_capability_refresh_is_driven_by_signed_expiry() -> None:
    near = worker._Capability.model_validate(
        _renewal() | {"expires_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat()}
    )
    far = worker._Capability.model_validate(
        _renewal() | {"expires_at": (datetime.now(UTC) + timedelta(minutes=4)).isoformat()}
    )

    assert worker._capability_needs_refresh(near)
    assert not worker._capability_needs_refresh(far)


async def test_request_enforces_total_wall_clock_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_wait(_seconds: float) -> None:
        return None

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        await asyncio.Event().wait()
        return httpx.Response(200)

    monkeypatch.setattr(worker.asyncio, "sleep", no_wait)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(worker.QaWorkerError):
            await worker._request(
                http,
                "GET",
                PRODUCTION_QA_CANARY_TASK_URL,
                request_timeout=timedelta(milliseconds=1),
            )
    assert attempts == worker._TRANSIENT_ATTEMPTS


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_at", (_NOW + timedelta(seconds=1)).isoformat()),
        ("started_at", _NOW.isoformat()),
    ],
)
def test_task_rejects_inverted_lifecycle_timestamps(field: str, value: str) -> None:
    payload = _task()
    payload[field] = value

    with pytest.raises(worker.ValidationError):
        worker._Task.model_validate(payload)


async def test_request_rejects_exhausted_overall_deadline() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_response_for)) as http:
        with pytest.raises(worker.QaWorkerError):
            await worker._request(
                http,
                "GET",
                PRODUCTION_QA_CANARY_TASK_URL,
                request_timeout=timedelta(seconds=30),
                deadline=time.monotonic(),
            )


async def test_worker_rejects_non_rotating_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == f"{PRODUCTION_QA_EXCHANGE_URL}?operation=renew":
            return httpx.Response(200, json=_renewal() | {"capability_token": "c" * 43})
        return _response_for(request)

    with pytest.raises(worker.QaWorkerError):
        await _run(monkeypatch, handler)


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["--help"], id="help"),
        pytest.param(["--run-id=secret"], id="runtime-override"),
        pytest.param(["secret"], id="positional"),
    ],
)
def test_cloud_entrypoint_rejects_all_runtime_arguments_without_echoing_values(
    arguments: list[str],
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.qa_document_transform_cloud", *arguments],
        cwd=Path(__file__).parents[1],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert not result.stdout
    assert json.loads(result.stderr) == {"error": "runtime_overrides_forbidden", "status": "failed"}
    assert "secret" not in result.stderr


def test_fixture_mutation_does_not_leak_between_cases() -> None:
    assert deepcopy(_evidence()) == _evidence()
