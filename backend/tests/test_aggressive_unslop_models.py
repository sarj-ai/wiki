from __future__ import annotations

from dataclasses import dataclass
import hashlib

from google.genai import types
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import parse_markdown
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.schemas import (
    PatchEnvelope,
    PatchReviewEnvelope,
    TextPatch,
    TextPatchReview,
    TransformPhase,
)
from tests.support import dependency


SOURCE = (
    "The hidden bird can sing from dense scrub beside the path while remaining unseen by observers."
)
REPLACEMENT = "The hidden bird can sing unseen from scrub beside the path."


class _ModelResponse:
    def __init__(self, response: types.GenerateContentResponse) -> None:
        self.response = response
        self.configurations: list[types.GenerateContentConfig] = []
        self.prompts: list[str] = []

    async def generate_content(
        self, *, model: str, contents: str, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        assert model
        self.prompts.append(contents)
        self.configurations.append(config)
        return self.response


@dataclass(slots=True)
class _AsyncClient:
    models: _ModelResponse


@dataclass(slots=True)
class _Client:
    aio: _AsyncClient


@dataclass(frozen=True, slots=True)
class _GenerationHarness:
    generator: VertexGeminiPatchGenerator
    call: _ModelResponse


def _generator(
    response: types.GenerateContentResponse,
) -> _GenerationHarness:
    call = _ModelResponse(response)
    policy = GeminiModelPolicy(
        project="test-project",
        location="global",
        unslop_model="gemini-3.8-flash",
        fallback_model="gemini-2.5-flash-lite",
        restyle_model="gemini-3.8-flash",
    )
    return _GenerationHarness(
        generator=VertexGeminiPatchGenerator(
            policy=policy, client=dependency(_Client(_AsyncClient(call)))
        ),
        call=call,
    )


async def test_aggressive_generation_uses_structured_output_and_supported_thinking() -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[patch])))
    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
    config = harness.call.configurations[0]
    assert config.response_schema is None
    assert config.response_json_schema == PatchEnvelope.model_json_schema()
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level is types.ThinkingLevel.LOW
    assert SOURCE in harness.call.prompts[0]
    assert "50–70% fewer words" in harness.call.prompts[0]
    assert "protected_suffix" in harness.call.prompts[0]


@pytest.mark.parametrize("approve", [True, False])
async def test_bound_semantic_verdict_is_respected_without_retrying_rejection(
    approve: bool,
) -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    review = TextPatchReview(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement_sha256=hashlib.sha256(REPLACEMENT.encode()).hexdigest(),
        equivalent=approve,
    )
    harness = _generator(
        types.GenerateContentResponse(parsed=PatchReviewEnvelope(reviews=[review]))
    )
    assert await harness.generator.verify(document, [patch]) == (
        frozenset({block.id}) if approve else frozenset()
    )
    assert len(harness.call.configurations) == 1


async def test_semantic_verdict_with_the_wrong_output_hash_fails_closed() -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    review = TextPatchReview(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement_sha256="0" * 64,
        equivalent=True,
    )
    harness = _generator(
        types.GenerateContentResponse(parsed=PatchReviewEnvelope(reviews=[review]))
    )
    with pytest.raises(ModelUnavailableError):
        await harness.generator.verify(document, [patch])
