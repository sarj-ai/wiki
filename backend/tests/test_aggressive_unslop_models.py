from __future__ import annotations

from dataclasses import dataclass
import hashlib

from google.genai import errors, types
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import parse_markdown
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.schemas import (
    PatchEnvelope,
    PatchReviewEnvelope,
    TextPatch,
    TextPatchReview,
    TransformFinding,
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
        self.models: list[str] = []
        self.failures = 0

    async def generate_content(
        self, *, model: str, contents: str, config: types.GenerateContentConfig
    ) -> types.GenerateContentResponse:
        assert model
        self.prompts.append(contents)
        self.configurations.append(config)
        self.models.append(model)
        if self.failures:
            self.failures -= 1
            raise errors.ClientError(429, {"error": {"message": "Offline transient fixture"}})
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
        fallback_model="gemini-3.1-flash-lite",
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
    assert config.response_json_schema is not None
    assert config.response_json_schema["additionalProperties"] is False
    assert "patches" in config.response_json_schema["properties"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level is types.ThinkingLevel.LOW
    assert SOURCE in harness.call.prompts[0]
    assert "50–70% fewer words" in harness.call.prompts[0]
    assert "protected_suffix" in harness.call.prompts[0]
    assert '"force_language":["can"]' in harness.call.prompts[0]


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
        reason="The bird's hidden singing location and ability are preserved.",
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
        reason="The bird's hidden singing location and ability are preserved.",
    )
    harness = _generator(
        types.GenerateContentResponse(parsed=PatchReviewEnvelope(reviews=[review]))
    )
    with pytest.raises(ModelUnavailableError):
        await harness.generator.verify(document, [patch])


async def test_missing_rewrite_proposals_retry_instead_of_silently_returning_no_edits() -> None:
    document = parse_markdown(SOURCE, title="Bird")
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[])))
    with pytest.raises(ModelUnavailableError):
        await harness.generator.generate(document, phase=TransformPhase.UNSLOP)
    assert len(harness.call.configurations) == 2


@pytest.mark.parametrize("code", ["force_language_changed", "semantic_review_rejected"])
async def test_model_repair_receives_the_exact_failure_and_original_source_constraints(
    code: str,
) -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    good = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    bad = good.model_copy(update={"replacement": REPLACEMENT.replace(" can ", " will ")})
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[good])))
    assert await harness.generator.repair(
        document,
        [bad],
        (
            TransformFinding(
                code=code,
                block_id=block.id,
                detail="The draft loses the hidden observer condition.",
            ),
        ),
    ) == [good]
    prompt = harness.call.prompts[0]
    assert SOURCE in prompt
    assert bad.replacement in prompt
    assert f'"rejected_by":["{code}"]' in prompt
    assert '"force_language":["can"]' in prompt
    assert '"force_language":["will"]' in prompt
    if code == "semantic_review_rejected":
        assert '"review_feedback":["The draft loses the hidden observer condition."]' in prompt


async def test_model_repair_does_not_submit_a_stale_source_proposal() -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    stale = TextPatch(block_id=block.id, expected_sha256="0" * 64, replacement=REPLACEMENT)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[])))
    assert (
        await harness.generator.repair(
            document,
            [stale],
            (TransformFinding(code="force_language_changed", block_id=block.id, detail="Skipped"),),
        )
        == []
    )
    assert not harness.call.configurations


@pytest.mark.parametrize("failures", [1, 3])
async def test_review_retries_keep_the_pinned_reviewer_instead_of_downgrading(
    failures: int,
) -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    review = TextPatchReview(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement_sha256=hashlib.sha256(REPLACEMENT.encode()).hexdigest(),
        equivalent=True,
        reason="The bird's hidden singing location and ability are preserved.",
    )
    harness = _generator(
        types.GenerateContentResponse(parsed=PatchReviewEnvelope(reviews=[review]))
    )
    harness.call.failures = failures
    if failures == 3:
        with pytest.raises(ModelUnavailableError):
            await harness.generator.verify(document, [patch])
    else:
        assert await harness.generator.verify(document, [patch]) == {block.id}
    assert harness.call.models == ["gemini-3.8-flash"] * (
        failures if failures == 3 else failures + 1
    )


async def test_sparse_proposals_share_a_review_batch_without_unchanged_prose() -> None:
    paragraph = SOURCE + " " + SOURCE + " " + SOURCE
    document = parse_markdown("\n\n".join(paragraph for _ in range(90)), title="Long fixture")
    selected = (document.blocks[0], document.blocks[-1])
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
        for block in selected
    ]
    reviews = PatchReviewEnvelope(
        reviews=[
            TextPatchReview(
                block_id=patch.block_id,
                expected_sha256=patch.expected_sha256,
                replacement_sha256=hashlib.sha256(patch.replacement.encode()).hexdigest(),
                equivalent=True,
                reason="Offline batch fixture preserves the bird's hidden singing context.",
            )
            for patch in patches
        ]
    )
    harness = _generator(types.GenerateContentResponse(parsed=reviews))
    assert await harness.generator.verify(document, patches) == {block.id for block in selected}
    assert len(harness.call.configurations) == 1
