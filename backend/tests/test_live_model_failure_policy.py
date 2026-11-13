import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
import json
from time import monotonic

from google import genai
from google.auth import exceptions as auth_errors
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
import pytest

from app.document_transform.errors import (
    ExhaustedModelError,
    ModelAuthenticationError,
    ModelConfigurationError,
    ModelDeadlineError,
    ModelOutputError,
    ModelPermissionError,
    ModelUnavailableError,
)
from app.document_transform.ir import parse_markdown
from app.document_transform.model_failures import (
    classify_model_failure,
    exhausted_model_failure,
    grouped_model_failure,
)
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.schemas import PatchEnvelope, TextPatch, TransformPhase
from app.performance import provider_retry_budget, provider_retry_remaining_seconds


_SECRET_SENTINEL = "PRIVATE_PROVIDER_MESSAGE_MUST_NOT_BE_LOGGED"
_SOURCE = "The gallery at 533 West 19th Street will not charge admission on November 14, 2026."


@dataclass
class _Run:
    generator: VertexGeminiPatchGenerator
    requests: list[httpx.Request]
    sleeps: list[float]


@dataclass(frozen=True)
class _ProviderConfig:
    model_id: str
    location: str
    endpoint: str
    token: str


_NORMAL_PROVIDER = _ProviderConfig(
    model_id="configured-writer",
    location="global",
    endpoint="http://127.0.0.1:9",
    token="offline-credential",
)


@asynccontextmanager
async def _provider(
    failures: tuple[int | str, ...],
    *,
    retry_after: str | None = None,
    call_seconds: float = 5,
    config: _ProviderConfig = _NORMAL_PROVIDER,
) -> AsyncGenerator[_Run]:
    requests: list[httpx.Request] = []
    sleeps: list[float] = []
    document = parse_markdown(_SOURCE, title="Provider fixture")
    block = document.blocks[0]
    envelope = PatchEnvelope(
        patches=[TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)]
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        failure = failures[len(requests) - 1] if len(requests) <= len(failures) else None
        if failure == "transport":
            raise httpx.ConnectError(_SECRET_SENTINEL, request=request)
        if failure == "stall":
            await asyncio.Event().wait()
        if isinstance(failure, int):
            return httpx.Response(
                failure,
                headers={"Retry-After": retry_after} if retry_after else {},
                json={"error": {"code": failure, "message": _SECRET_SENTINEL}},
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": "malformed output"
                                    if failure in {"malformed", "truncated"}
                                    else envelope.model_dump_json()
                                }
                            ],
                        },
                        "finishReason": "MAX_TOKENS" if failure == "truncated" else "STOP",
                    }
                ]
            },
        )

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await asyncio.sleep(0)

    policy = GeminiModelPolicy(
        project="offline-fault-injection",
        location="global",
        unslop_model=config.model_id,
        fallback_model="configured-fallback",
        restyle_model="configured-reviewer",
        timeout=timedelta(seconds=call_seconds),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = genai.Client(
            vertexai=True,
            project=policy.project,
            location=config.location,
            credentials=Credentials(token=config.token),
            http_options=types.HttpOptions(base_url=config.endpoint, httpx_async_client=http),
        )
        try:
            yield _Run(
                VertexGeminiPatchGenerator(policy=policy, client=client, writer_sleep=sleep),
                requests,
                sleeps,
            )
        finally:
            await client.aio.aclose()


async def _generate(run: _Run) -> list[TextPatch]:
    return await run.generator.generate(
        parse_markdown(_SOURCE, title="Provider fixture"), phase=TransformPhase.UNSLOP
    )


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (404, "model_configuration_invalid"),
        (401, "model_authentication_failed"),
        (403, "model_permission_denied"),
        (400, "model_configuration_invalid"),
    ],
    ids=[
        "12-invalid-model",
        "13-invalid-credentials",
        "14-permission-denied",
        "15-endpoint-region",
    ],
)
async def test_permanent_provider_rejection_stops_before_fallback_or_queue_retry(
    status: int, code: str
) -> None:
    async with _provider((status, status, status)) as run:
        with pytest.raises(ModelUnavailableError) as caught:
            await _generate(run)
        assert caught.value.code == code
        assert not caught.value.retryable
        assert len(run.requests) == 1
        assert not run.sleeps
        assert _SECRET_SENTINEL not in str(caught.value)


@pytest.mark.parametrize("fault", ["model", "credential", "endpoint", "region"])
async def test_injected_provider_configuration_reaches_the_actual_sdk_request_and_fails_once(
    fault: str,
) -> None:
    match fault:
        case "model":
            config = replace(_NORMAL_PROVIDER, model_id="model-does-not-exist-for-qa")
            status, code = 404, "model_configuration_invalid"
        case "credential":
            config = replace(_NORMAL_PROVIDER, token="invalid-offline-credential")
            status, code = 401, "model_authentication_failed"
        case "endpoint":
            config = replace(
                _NORMAL_PROVIDER, endpoint="https://incorrect-provider-endpoint.invalid"
            )
            status, code = 400, "model_configuration_invalid"
        case _:
            config = replace(_NORMAL_PROVIDER, location="invalid-region-qa")
            status, code = 400, "model_configuration_invalid"
    async with _provider((status,), config=config) as run:
        with pytest.raises(ModelUnavailableError) as caught:
            await _generate(run)
        assert caught.value.code == code
        assert not caught.value.retryable
        (request,) = run.requests
        assert f"/locations/{config.location}/" in request.url.path
        assert request.url.path.endswith(f"/models/{config.model_id}:generateContent")
        assert request.url.host in {"127.0.0.1", "incorrect-provider-endpoint.invalid"}
        assert request.headers["authorization"] == f"Bearer {config.token}"
        assert config.token not in str(caught.value)


async def test_16_quota_retry_after_reaches_the_scheduler_without_sdk_retry_multiplication() -> (
    None
):
    async with _provider((429,), retry_after="8") as run:
        assert len(await _generate(run)) == 1
        assert len(run.requests) == 2
        assert max(run.sleeps) >= 8


async def test_17_server_errors_use_at_most_three_total_http_attempts() -> None:
    async with _provider((503, 503)) as run:
        assert len(await _generate(run)) == 1
        assert len(run.requests) == 3
        assert sum(run.sleeps) >= 6


async def test_exhausted_quota_attempts_do_not_allow_queue_delivery_to_reset_provider_backoff() -> (
    None
):
    async with _provider((429, 429, 429), retry_after="8") as run:
        with pytest.raises(ModelUnavailableError) as caught:
            await _generate(run)
        assert len(run.requests) == 3
        assert not caught.value.retryable
        assert caught.value.retry_after == 8


async def test_token_limited_provider_response_is_terminal_without_identical_retry() -> None:
    async with _provider(("truncated",)) as run:
        with pytest.raises(ModelUnavailableError) as caught:
            await _generate(run)
        assert caught.value.code == "model_output_truncated"
        assert not caught.value.retryable
        assert len(run.requests) == 1


async def test_primary_pro_request_leaves_room_for_complete_json_and_keeps_independent_review_separate() -> (
    None
):
    config = replace(_NORMAL_PROVIDER, model_id="gemini-3.1-pro-preview")
    async with _provider((), config=config) as run:
        assert await _generate(run)
        request = json.loads(run.requests[0].content)
        assert request["generationConfig"]["maxOutputTokens"] == 32_768
        generation = types.GenerateContentConfig.model_validate(request["generationConfig"])
        assert generation.thinking_config is not None
        assert generation.thinking_config.thinking_level == types.ThinkingLevel.HIGH


def test_known_permanent_batch_failure_retains_actionable_category_after_other_attempts_exhaust() -> (
    None
):
    failure = grouped_model_failure(
        ExceptionGroup("parallel batches", [ExhaustedModelError(), ModelPermissionError()])
    )
    assert failure.code == "model_permission_denied"
    assert not failure.retryable


async def test_18_transport_failure_is_classified_and_retried_with_no_local_rewrite() -> None:
    async with _provider(("transport",)) as run:
        patches = await _generate(run)
        assert len(run.requests) == 2
        assert patches[0].replacement == _SOURCE


async def test_19_stalled_provider_does_not_start_another_call_without_remaining_budget() -> None:
    async with _provider(("stall", "stall", "stall"), call_seconds=0.04) as run:
        started = monotonic()
        with (
            provider_retry_budget(timedelta(seconds=31.2)),
            pytest.raises(ModelUnavailableError) as caught,
        ):
            await _generate(run)
        assert caught.value.code == "model_deadline_exhausted"
        assert not caught.value.retryable
        assert monotonic() - started < 1
        assert len(run.requests) == 1


async def test_20_malformed_response_is_not_reported_as_model_unavailability() -> None:
    async with _provider(("malformed",) * 3) as run:
        with pytest.raises(ModelUnavailableError) as caught:
            await _generate(run)
        assert caught.value.code == "model_output_invalid"
        assert not caught.value.retryable
        assert len(run.requests) == 3


def test_21_nested_provider_budgets_cannot_extend_the_parent_deadline() -> None:
    now = [0.0]
    with provider_retry_budget(timedelta(seconds=100), clock=lambda: now[0]):
        now[0] = 80
        with provider_retry_budget(timedelta(minutes=4)):
            assert provider_retry_remaining_seconds() == 20
        assert provider_retry_remaining_seconds() == 20


async def test_22_retry_after_larger_than_the_budget_stops_without_an_early_retry() -> None:
    async with _provider((429,), retry_after="600") as run:
        with (
            provider_retry_budget(timedelta(minutes=4)),
            pytest.raises(ModelUnavailableError) as caught,
        ):
            await _generate(run)
        assert not caught.value.retryable
        assert len(run.requests) == 1
        assert not run.sleeps


@pytest.mark.parametrize("retryable", [False, True])
def test_credential_refresh_failure_retains_the_auth_library_retry_policy(
    *, retryable: bool
) -> None:
    failure = classify_model_failure(
        auth_errors.RefreshError(_SECRET_SENTINEL, retryable=retryable)
    )
    assert failure is not None
    assert failure.retryable is retryable
    assert failure.code == ("model_unavailable" if retryable else "model_authentication_failed")
    assert _SECRET_SENTINEL not in str(failure)


@pytest.mark.parametrize(
    "failure",
    [
        ModelAuthenticationError(),
        ModelConfigurationError(),
        ModelDeadlineError(),
        ModelOutputError(),
        ModelPermissionError(),
    ],
)
def test_terminal_model_policy_cannot_be_mutated_into_a_queue_retry(
    failure: ModelUnavailableError,
) -> None:
    assert failure.retryable is False
    assert failure.retry_after is None
    assert failure.retry_within_operation is isinstance(failure, ModelOutputError)
    for attribute, value in (("retryable", True), ("retry_after", 1.0)):
        with pytest.raises(AttributeError):
            setattr(failure, attribute, value)


@pytest.mark.parametrize("delay", [-1.0, float("inf"), float("nan")])
def test_model_retry_delay_rejects_invalid_provider_timing(delay: float) -> None:
    with pytest.raises(ValueError, match="finite and nonnegative"):
        ModelUnavailableError(retry_after=delay)


def test_exhausted_transient_wrapper_preserves_delay_without_aliasing_the_original() -> None:
    original = ModelUnavailableError(retry_after=8.0)
    wrapped = exhausted_model_failure(original)
    assert wrapped is not original
    assert isinstance(wrapped, ExhaustedModelError)
    assert wrapped.retryable is False
    assert wrapped.retry_within_operation is False
    assert wrapped.retry_after == 8.0
    assert wrapped.__cause__ is None


def test_exhausted_failure_stays_terminal_when_wrapped_again() -> None:
    original = ExhaustedModelError(retry_after=8.0)
    wrapped = exhausted_model_failure(original)
    assert isinstance(wrapped, ExhaustedModelError)
    assert wrapped is not original
    assert wrapped.retry_after == 8.0
    assert not wrapped.retryable
    assert not wrapped.retry_within_operation


def test_unknown_exhausted_failure_cannot_restart_provider_attempts() -> None:
    wrapped = exhausted_model_failure(None)
    assert isinstance(wrapped, ExhaustedModelError)
    assert not wrapped.retryable
    assert wrapped.retry_after is None
