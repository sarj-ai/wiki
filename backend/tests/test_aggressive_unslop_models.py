from __future__ import annotations

from dataclasses import dataclass
import hashlib

from google.genai import errors, types
from pydantic import BaseModel, ConfigDict
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


class _ReviewInputPair(BaseModel):
    model_config = ConfigDict(extra="ignore")
    block_id: str
    expected_sha256: str
    replacement_sha256: str
    source: str
    context_id: str


class _ReviewInputContext(BaseModel):
    context_id: str
    source: str
    proposed: str


class _ReviewInput(BaseModel):
    document_contexts: list[_ReviewInputContext]
    document_proposals: list[_ReviewInputPair]


class _ModelResponse:
    def __init__(self, response: types.GenerateContentResponse) -> None:
        self.response = response
        self.configurations: list[types.GenerateContentConfig] = []
        self.prompts: list[str] = []
        self.models: list[str] = []
        self.failures = 0
        self.review_input = False
        self.rejected_sources: set[str] = set()
        self.omitted_sources: set[str] = set()

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
        if self.review_input:
            payload = _ReviewInput.model_validate_json(contents)
            return types.GenerateContentResponse(
                parsed=PatchReviewEnvelope(
                    reviews=[
                        TextPatchReview(
                            block_id=pair.block_id,
                            expected_sha256=pair.expected_sha256,
                            replacement_sha256=pair.replacement_sha256,
                            equivalent=pair.source not in self.rejected_sources,
                            reason="The fixture preserves the bird's hidden singing location and ability.",
                        )
                        for pair in payload.document_proposals
                        if pair.source not in self.omitted_sources
                    ]
                )
            )
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
        restyle_model="gemini-3.1-pro-preview",
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
    assert config.thinking_config.thinking_level is types.ThinkingLevel.MEDIUM
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
    assert len(harness.call.configurations) == 3


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
    assert harness.call.models == ["gemini-3.1-pro-preview"]
    config = harness.call.configurations[0]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level is types.ThinkingLevel.HIGH
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
    assert harness.call.models == ["gemini-3.1-pro-preview"] * (
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
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    assert await harness.generator.verify(document, patches) == {block.id for block in selected}
    assert len(harness.call.configurations) == 1
    payload = _ReviewInput.model_validate_json(harness.call.prompts[0])
    submitted = payload.document_proposals
    assert len(submitted) == 6
    assert len(payload.document_contexts) == 1
    context = payload.document_contexts[0]
    assert context.source == paragraph
    assert context.proposed == REPLACEMENT
    assert all(
        pair.source == SOURCE and pair.context_id == context.context_id for pair in submitted
    )


async def test_review_batches_bound_paired_text_without_losing_or_duplicating_verdicts() -> None:
    document = parse_markdown("\n\n".join(SOURCE for _ in range(13)), title="Article")
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
        for block in document.blocks
        if block.text.strip()
    ]
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    assert await harness.generator.verify(document, patches) == {
        patch.block_id for patch in patches
    }
    submitted = [_ReviewInput.model_validate_json(prompt) for prompt in harness.call.prompts]
    assert len(submitted) > 1
    assert all(len(batch.document_proposals) <= 12 for batch in submitted)
    assert all(len(prompt) <= 8_000 for prompt in harness.call.prompts)
    assert [pair.block_id for batch in submitted for pair in batch.document_proposals] == [
        patch.block_id for patch in patches
    ]
    assert harness.call.models == ["gemini-3.1-pro-preview"] * len(submitted)


@pytest.mark.parametrize("verdict", ["approve", "reject", "omit"])
async def test_every_source_sentence_must_pass_before_its_paragraph_is_accepted(
    verdict: str,
) -> None:
    provenance = "Its measurements come from particular birds and particular methods."
    source = (
        "Its song belongs within a breeding season, a territory and an annual journey. "
        + provenance
    )
    document = parse_markdown(source, title="Provenance")
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement="A bird's song and measurements reflect its breeding cycle and migration.",
    )
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    if verdict == "reject":
        harness.call.rejected_sources.add(provenance)
    if verdict == "omit":
        harness.call.omitted_sources.add(provenance)
        with pytest.raises(ModelUnavailableError):
            await harness.generator.review(document, [patch])
        return
    decision = await harness.generator.review(document, [patch])
    assert decision.approved == (frozenset({block.id}) if verdict == "approve" else frozenset())
    assert set(decision.rejected) == ({block.id} if verdict == "reject" else set())
    payload = _ReviewInput.model_validate_json(harness.call.prompts[0])
    submitted = payload.document_proposals
    assert len(submitted) == 2
    assert {pair.source for pair in submitted} == {
        "Its song belongs within a breeding season, a territory and an annual journey.",
        provenance,
    }
    assert len(payload.document_contexts) == 1
    context = payload.document_contexts[0]
    assert context.source == source
    assert context.proposed == patch.replacement
    assert all(pair.context_id == context.context_id for pair in submitted)
    assert len({pair.block_id for pair in submitted}) == 2


async def test_sentence_verdict_hashes_bind_context_and_position() -> None:
    shared = "Its measurements come from particular birds and particular methods."
    first = parse_markdown(SOURCE + " " + shared, title="First")
    second = parse_markdown(shared + " " + SOURCE, title="Second")
    hashes: list[str] = []
    for document in (first, second):
        block = document.blocks[0]
        patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
        harness = _generator(types.GenerateContentResponse())
        harness.call.review_input = True
        await harness.generator.review(document, [patch])
        submitted = _ReviewInput.model_validate_json(harness.call.prompts[0]).document_proposals
        hashes.append(next(pair.expected_sha256 for pair in submitted if pair.source == shared))
    assert hashes[0] != hashes[1]


async def test_sentence_checks_share_paragraph_context_without_losing_full_coverage() -> None:
    paragraphs = [f"Case {index}: " + " ".join([SOURCE] * 5) for index in range(12)]
    document = parse_markdown("\n\n".join(paragraphs), title="Context reuse")
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
        for block in document.blocks
        if block.text.strip()
    ]
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    assert await harness.generator.verify(document, patches) == {
        patch.block_id for patch in patches
    }
    payloads = [_ReviewInput.model_validate_json(prompt) for prompt in harness.call.prompts]
    assert sum(len(payload.document_proposals) for payload in payloads) == 60
    assert len(payloads) <= 6
    assert all(len(prompt) <= 8_000 for prompt in harness.call.prompts)
    for payload in payloads:
        contexts = {context.context_id: context for context in payload.document_contexts}
        assert len(contexts) == len(payload.document_contexts)
        assert len(contexts) < len(payload.document_proposals)
        assert all(
            contexts[pair.context_id].source in paragraphs for pair in payload.document_proposals
        )
        assert all(context.proposed == REPLACEMENT for context in contexts.values())
