from __future__ import annotations

import uuid

from google.genai import errors as genai_errors, types as genai_types
from pydantic import JsonValue
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock
from app.document_transform.models import (
    GeminiModelPolicy,
    VertexGeminiPatchGenerator,
)
from app.document_transform.schemas import PatchEnvelope, TextPatch, TransformPhase
from app.identifiers import ModelBatchJobId, ModelCallId, ModelId, TaskId
from app.model_batches.ledger import (
    BatchPacing,
    ModelBatchPendingError,
    ModelCallKey,
    ModelCallLedger,
    OnlinePacing,
    collect_pending,
    current_model_pacing,
    model_call_ledger_scope,
    require_answered,
)
from app.model_batches.requests import ModelRequest, batch_line_call_id, model_request
from app.model_batches.states import (
    FailedModelCall,
    FailedModelOutcome,
    ModelCall,
    ModelCallFailure,
    QueuedModelCall,
    SucceededModelCall,
    SucceededModelOutcome,
)
from tests.support import dependency


_TASK = TaskId(uuid.uuid7())
_MODEL = "gemini-3.1-pro-preview"


def _config(
    *, http_options: genai_types.HttpOptions | None = None, temperature: float | None = None
) -> genai_types.GenerateContentConfig:
    return genai_types.GenerateContentConfig(
        system_instruction="Edit carefully.",
        response_mime_type="application/json",
        response_json_schema={"type": "object"},
        max_output_tokens=1024,
        thinking_config=genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.HIGH),
        http_options=http_options,
        temperature=temperature,
    )


def _request(contents: str = "{}") -> ModelRequest:
    return model_request(operation="unslop", model=_MODEL, contents=contents, config=_config())


def test_request_identity_ignores_transport_timeouts() -> None:
    short = model_request(
        operation="review",
        model=_MODEL,
        contents="x",
        config=_config(http_options=genai_types.HttpOptions(timeout=1_000)),
    )
    long = model_request(
        operation="review",
        model=_MODEL,
        contents="x",
        config=_config(http_options=genai_types.HttpOptions(timeout=90_000)),
    )

    assert short.request_sha256 == long.request_sha256
    assert short.request_sha256 != _request("y").request_sha256


def test_request_rejects_settings_a_batch_line_would_drop() -> None:
    with pytest.raises(ValueError, match="Unsupported Gemini request settings"):
        model_request(
            operation="unslop", model=_MODEL, contents="x", config=_config(temperature=0.2)
        )


def test_vertex_batch_line_carries_the_call_label() -> None:
    call_id = ModelCallId(uuid.uuid7())

    line = _request("hello").vertex_batch_line(call_id)

    assert batch_line_call_id(line) == call_id.hex
    request = line["request"]
    assert isinstance(request, dict)
    assert request["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert request["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseJsonSchema": {"type": "object"},
        "maxOutputTokens": 1024,
        "thinkingConfig": {"thinkingLevel": "HIGH"},
    }


def _schema_keys(node: JsonValue) -> set[str]:
    match node:
        case dict():
            return set(node) | {key for value in node.values() for key in _schema_keys(value)}
        case list():
            return {key for item in node for key in _schema_keys(item)}
        case _:
            return set()


def test_vertex_batch_line_inlines_schema_references() -> None:
    schema = PatchEnvelope.model_json_schema()
    config = _config().model_copy(update={"response_json_schema": schema})
    request = model_request(operation="unslop", model=_MODEL, contents="x", config=config)

    line = request.vertex_batch_line(ModelCallId(uuid.uuid7()))

    assert "$defs" in schema
    assert not {key for key in _schema_keys(line) if key.startswith("$")}
    assert request.response_json_schema == schema


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#/$defs/Missing"},
        {
            "$defs": {"Node": {"properties": {"next": {"$ref": "#/$defs/Node"}}}},
            "$ref": "#/$defs/Node",
        },
    ],
)
def test_vertex_batch_line_rejects_unresolvable_references(
    schema: dict[str, JsonValue],
) -> None:
    config = _config().model_copy(update={"response_json_schema": schema})
    request = model_request(operation="unslop", model=_MODEL, contents="x", config=config)

    with pytest.raises(ValueError, match="Cannot inline"):
        request.vertex_batch_line(ModelCallId(uuid.uuid7()))


def _succeeded(request: ModelRequest, occurrence: int, text: str) -> SucceededModelCall:
    response: dict[str, JsonValue] = {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
        ]
    }
    return SucceededModelCall(
        id=ModelCallId(uuid.uuid7()),
        task_id=_TASK,
        request_sha256=request.request_sha256,
        occurrence=occurrence,
        model=ModelId(_MODEL),
        job_id=ModelBatchJobId(uuid.uuid7()),
        outcome=SucceededModelOutcome(response=response),
    )


def _failed(request: ModelRequest, *, occurrence: int, code: int, message: str) -> FailedModelCall:
    return FailedModelCall(
        id=ModelCallId(uuid.uuid7()),
        task_id=_TASK,
        request_sha256=request.request_sha256,
        occurrence=occurrence,
        model=ModelId(_MODEL),
        job_id=ModelBatchJobId(uuid.uuid7()),
        outcome=FailedModelOutcome(failure=ModelCallFailure(code=code, message=message)),
    )


def _ledger(*calls: ModelCall) -> ModelCallLedger:
    entries: dict[ModelCallKey, ModelCall] = {
        (call.request_sha256, call.occurrence): call for call in calls
    }
    return ModelCallLedger(entries)


def test_ledger_reports_a_miss_with_its_occurrence() -> None:
    ledger = _ledger()

    with pytest.raises(ModelBatchPendingError) as first:
        ledger.resolve(_request())
    with pytest.raises(ModelBatchPendingError) as second:
        ledger.resolve(_request())

    assert [miss.occurrence for miss in first.value.misses] == [1]
    assert [miss.occurrence for miss in second.value.misses] == [2]


def test_ledger_replays_success_then_distinguishes_a_repeat() -> None:
    request = _request()
    ledger = _ledger(_succeeded(request, 1, '{"ok":true}'))

    assert ledger.resolve(request).text == '{"ok":true}'
    with pytest.raises(ModelBatchPendingError):
        ledger.resolve(request)


def test_ledger_replays_a_failure_as_the_provider_error() -> None:
    request = _request()
    failed = _failed(request, occurrence=1, code=429, message="quota")

    with pytest.raises(genai_errors.APIError) as error:
        _ledger(failed).resolve(request)

    assert error.value.code == 429


def test_queued_call_is_still_pending() -> None:
    request = _request()
    queued = QueuedModelCall(
        id=ModelCallId(uuid.uuid7()),
        task_id=_TASK,
        request_sha256=request.request_sha256,
        occurrence=1,
        model=ModelId(_MODEL),
        job_id=ModelBatchJobId(uuid.uuid7()),
    )

    with pytest.raises(ModelBatchPendingError):
        _ledger(queued).resolve(request)


async def test_fan_out_collects_every_branch_miss() -> None:
    ledger = _ledger()

    async def miss(contents: str) -> int:
        ledger.resolve(_request(contents))
        return 0

    async def answer() -> int:
        return 1

    results = [
        await collect_pending(miss("a")),
        await collect_pending(answer()),
        await collect_pending(miss("b")),
    ]

    with pytest.raises(ModelBatchPendingError) as pending:
        require_answered(results)

    assert len(pending.value.misses) == 2


def test_pacing_follows_the_transport_in_scope() -> None:
    assert current_model_pacing() == OnlinePacing()
    with model_call_ledger_scope(_ledger()):
        assert current_model_pacing() == BatchPacing(editorial_passes=2, layout_passes=1)


def _document(blocks: int) -> DocumentIR:
    return DocumentIR(
        title="Replay",
        blocks=tuple(
            TextBlock(
                id=f"b{index}",
                kind=BlockKind.PARAGRAPH,
                text=f"This paragraph number {index} explains the rollout plan in detail.",
            )
            for index in range(blocks)
        ),
        source_format="markdown",
    )


def _generator() -> VertexGeminiPatchGenerator:
    return VertexGeminiPatchGenerator(
        policy=GeminiModelPolicy(
            project="test-project",
            location="global",
            unslop_model=_MODEL,
            fallback_model=_MODEL,
            restyle_model=_MODEL,
        ),
        client=dependency(object()),
    )


async def test_generator_round_collects_every_editor_batch_into_one_suspension() -> None:
    document = _document(45)

    with model_call_ledger_scope(_ledger()), pytest.raises(ModelBatchPendingError) as pending:
        await _generator().generate(document, phase=TransformPhase.UNSLOP)

    misses = pending.value.misses
    assert len(misses) == 2
    assert {miss.occurrence for miss in misses} == {1}

    answers = []
    for miss in misses:
        envelope = PatchEnvelope(
            patches=[
                TextPatch(
                    block_id=block.id,
                    expected_sha256=block.sha256,
                    replacement=block.text.replace(" in detail", ""),
                )
                for block in document.blocks
                if block.text in miss.request.contents
            ]
        )
        answers.append(_succeeded(miss.request, 1, envelope.model_dump_json()))

    with model_call_ledger_scope(_ledger(*answers)):
        patches = await _generator().generate(document, phase=TransformPhase.UNSLOP)

    assert len(patches) == 45
    assert all(" in detail" not in patch.replacement for patch in patches)


async def test_failed_batch_answer_moves_to_the_next_attempt() -> None:
    document = _document(3)
    with model_call_ledger_scope(_ledger()), pytest.raises(ModelBatchPendingError) as first:
        await _generator().generate(document, phase=TransformPhase.UNSLOP)
    (miss,) = first.value.misses
    failed = _failed(miss.request, occurrence=1, code=503, message="busy")

    with model_call_ledger_scope(_ledger(failed)), pytest.raises(ModelBatchPendingError) as retry:
        await _generator().generate(document, phase=TransformPhase.UNSLOP)

    (repeat,) = retry.value.misses
    # All three policy models are identical, so the retry is the same request's
    # second occurrence rather than an endless replay of the recorded failure.
    assert repeat.request.request_sha256 == miss.request.request_sha256
    assert repeat.occurrence == 2


async def test_exhausted_batch_failures_surface_as_unavailable() -> None:
    document = _document(3)
    with model_call_ledger_scope(_ledger()), pytest.raises(ModelBatchPendingError) as first:
        await _generator().generate(document, phase=TransformPhase.UNSLOP)
    request = first.value.misses[0].request
    failures = [
        _failed(request, occurrence=occurrence, code=503, message="busy")
        for occurrence in (1, 2, 3)
    ]

    with model_call_ledger_scope(_ledger(*failures)), pytest.raises(ModelUnavailableError):
        await _generator().generate(document, phase=TransformPhase.UNSLOP)
