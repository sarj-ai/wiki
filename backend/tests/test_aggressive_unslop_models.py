from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
from http import HTTPStatus
import json
from typing import Literal

from google import genai
from google.genai import errors, types
from google.oauth2.credentials import Credentials
import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel
import pytest

from app.document_settings import DocumentSettings
from app.document_transform import models as transform_models
from app.document_transform.dependencies import document_model_policy
from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import BlockKind, parse_google_document, parse_markdown
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import RewriteConstraints, rewrite_constraints, validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    PatchEnvelope,
    PatchReviewEnvelope,
    TextPatch,
    TextPatchReview,
    TransformFinding,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import (
    UNSLOP_POLICY_VERSION,
    UNSLOP_REVIEW_INSTRUCTION,
    UNSLOP_REWRITE_INSTRUCTION,
    concision_target,
    model_editable_blocks,
    without_editorial_framing,
)
from app.identifiers import FileId
from tests.support import dependency


SOURCE = (
    "The hidden bird can sing from dense scrub beside the path while remaining unseen by observers."
)
REPLACEMENT = "The hidden bird can sing unseen from scrub beside the path."


_SRE_REVIEW_PARAGRAPHS = (
    (
        "At a broad and overall level, this working note is intended to bring together the "
        "latency estimates in the Google SRE reference so that the same reference information "
        "can be read in one place. The figures are rules of thumb, and it is useful to say that "
        "point explicitly rather than leave the point implicit. They are reference estimates "
        "rather than a new set of measurements of current hardware. A modern presentation of the "
        "figures does not turn the figures into modern benchmarks. In the table, the original "
        "nanosecond values remain the basis for comparison. The millisecond column is a "
        "calculated unit conversion, including the first five entries for which the source "
        "leaves that column blank. The distinction between a source value and a calculated "
        "conversion is worth keeping clear throughout this note. This draft takes a slow walk "
        "into the theme and lets the prose linger in its own warm glow. The text spends time on "
        "a soft lead in and a broad sweep of scene setting. A long, calm flow of words gives the "
        "page an air of care and a sense of ease. The voice pauses to greet the reader, then "
        "drifts through a gentle turn of phrase. A few words of pure scene setting lead into a "
        "fresh burst of verbal flair. The prose seems keen to dwell on the act of writing and "
        "the feel of the page. It rests in a calm mood, adds a soft verbal bow, and takes its "
        "time with the next turn. This is a stretch of draft stage chatter, rich in warm tones "
        "and slow, smooth prose. A long lead in gives way to a brief pause, then to a broad wash "
        "of soft prose. The close adds a calm nod and a light sweep of the pen. A last soft note "
        "of pure prose rounds off this long and warm lead in."
    ),
    (
        "When looking across the smaller operations, the reference gives 1 ns for an L1 cache "
        "lookup, 3 ns for a branch prediction miss, 4 ns for L2, 17 ns for a mutex lock and "
        "unlock, and 100 ns for a main-memory reference. These are different operations, so the "
        "intention is to preserve the differences rather than describe them as the same "
        "operation. Moving beyond these entries, the reference includes Zippy compression, "
        "sequential memory reading, a small network send, and an SSD random read. Each entry "
        "keeps its stated amount of data and, where relevant, its stated network rate. A value "
        "without that surrounding description would lose part of the reference, so the "
        "surrounding description is retained here as well as in the table. The draft wraps this "
        "part in a soft layer of prose and takes a long route through its own lead in. A calm "
        "voice drifts across the page, with a broad smile in the phrasing and a warm tone in the "
        "flow. The text makes a small turn, rests for a beat, then adds a fresh line of scene "
        "setting. This is prose that likes a slow stroll and a wide view of its own style. A "
        "gentle pause leads to a light verbal bow, followed by a long sweep of draft stage "
        "chatter. The words keep the mood soft and the pace calm as the page moves through its "
        "lead in. A warm air of care hangs over the text, while the voice spends time on the "
        "feel of the phrasing. The prose takes a scenic path through this small bit of scene "
        "setting and dwells on the art of the lead in. A calm close adds a soft nod, a warm "
        "glow, and a last sweep of the pen. A light verbal nod lends the close a soft glow."
    ),
)


class _ReviewInputPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    expected_sha256: str
    replacement_sha256: str
    source_sentence: str
    context_id: str
    source_kind: Literal["sentence", "clause", "dependent_clause"]
    source_span: tuple[int, int] | None
    parent_sentence: str | None


class _ReviewInputContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_id: str
    source_block: str
    proposed_block: str
    block_kind: BlockKind


class _ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_contexts: list[_ReviewInputContext]
    sentence_audits: list[_ReviewInputPair]


class _ConcisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_words: int
    max_words: int
    draft_target_words: int


class _RewriteInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    block_id: str
    sha256: str
    text: str
    kind: BlockKind
    rewrite_constraints: RewriteConstraints
    concision_target: _ConcisionInput | None


class _RejectedInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    block_id: str
    proposed: str
    rejected_constraints: RewriteConstraints
    review_feedback: list[str]


class _RewriteInputs(RootModel[list[_RewriteInput]]):
    """Source blocks and their authoritative rewrite constraints."""


class _RejectedInputs(RootModel[list[_RejectedInput]]):
    """Earlier unsafe proposals and their observed constraints."""


class _WriterRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    generation_config: types.GenerateContentConfig = Field(alias="generationConfig")


class _ModelResponse:
    def __init__(self, response: types.GenerateContentResponse) -> None:
        self.response = response
        self.configurations: list[types.GenerateContentConfig] = []
        self.prompts: list[str] = []
        self.models: list[str] = []
        self.failures = 0
        self.failure_status = 429
        self.timeout_failures = False
        self.review_input = False
        self.rewrite_input = False
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
            if self.timeout_failures:
                raise TimeoutError
            error = (
                errors.ServerError
                if self.failure_status >= HTTPStatus.INTERNAL_SERVER_ERROR
                else errors.ClientError
            )
            raise error(self.failure_status, {"error": {"message": "Offline provider fixture"}})
        if self.rewrite_input:
            blocks = _RewriteInputs.model_validate_json(
                next(line for line in contents.splitlines() if line.startswith("["))
            ).root
            return types.GenerateContentResponse(
                parsed=PatchEnvelope(
                    patches=[
                        TextPatch(
                            block_id=block.block_id,
                            expected_sha256=block.sha256,
                            replacement=block.text,
                        )
                        for block in blocks
                    ]
                )
            )
        if self.review_input:
            payload = _ReviewInput.model_validate_json(contents)
            return types.GenerateContentResponse(
                parsed=PatchReviewEnvelope(
                    reviews=[
                        TextPatchReview(
                            block_id=pair.block_id,
                            expected_sha256=pair.expected_sha256,
                            replacement_sha256=pair.replacement_sha256,
                            equivalent=pair.source_sentence not in self.rejected_sources,
                            reason="The fixture preserves the bird's hidden singing location and ability.",
                        )
                        for pair in payload.sentence_audits
                        if pair.source_sentence not in self.omitted_sources
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
            policy=policy,
            client=dependency(_Client(_AsyncClient(call))),
            writer_sleep=_yield_writer_sleep,
        ),
        call=call,
    )


async def _yield_writer_sleep(_delay: float) -> None:
    await asyncio.sleep(0)


@pytest.mark.parametrize(
    ("model_override", "expected_model", "thinking_level"),
    [
        pytest.param(None, "gemini-3.1-pro-preview", types.ThinkingLevel.HIGH, id="normal-default"),
        pytest.param(
            "gemini-3.8-flash",
            "gemini-3.8-flash",
            types.ThinkingLevel.LOW,
            id="explicit-3.8-override",
        ),
    ],
)
async def test_normal_model_settings_reach_the_real_sdk_without_overriding_explicit_models(
    model_override: str | None,
    expected_model: str,
    thinking_level: types.ThinkingLevel | None,
) -> None:
    settings = (
        DocumentSettings()
        if model_override is None
        else DocumentSettings(TRANSFORM_UNSLOP_MODEL=model_override)
    )
    policy = document_model_policy(project="offline-fixture", document=settings)
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    models: list[str] = []
    configurations: list[types.GenerateContentConfig] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent"))
        configurations.append(_WriterRequest.model_validate_json(request.content).generation_config)
        assert request.headers["X-Server-Timeout"] == "90"
        assert request.extensions["timeout"] == dict.fromkeys(
            ("connect", "read", "write", "pool"), 90
        )
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": PatchEnvelope(patches=[patch]).model_dump_json()}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = genai.Client(
            vertexai=True,
            project=policy.project,
            location=policy.location,
            credentials=Credentials(token="offline-fixture-not-a-real-token"),
            http_options=types.HttpOptions(
                base_url="http://127.0.0.1:9",
                httpx_async_client=http,
            ),
        )
        generator = VertexGeminiPatchGenerator(policy=policy, client=client)
        try:
            assert await generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
        finally:
            await client.aio.aclose()

    assert models == [expected_model]
    (config,) = configurations
    assert config.max_output_tokens == 16_384
    if thinking_level is None:
        assert config.thinking_config is None
    else:
        assert config.thinking_config is not None
        assert config.thinking_config.thinking_level == thinking_level


async def test_aggressive_generation_uses_structured_output_and_supported_thinking() -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement=REPLACEMENT,
        rationale="x" * 240,
    )
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[patch])))
    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
    config = harness.call.configurations[0]
    assert config.response_schema is None
    assert config.response_json_schema is not None
    assert config.max_output_tokens == 16_384
    assert config.response_json_schema["additionalProperties"] is False
    assert "patches" in config.response_json_schema["properties"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level is types.ThinkingLevel.LOW
    assert SOURCE in harness.call.prompts[0]
    assert "Each rationale must be at most 240 characters." in harness.call.prompts[0]
    assert isinstance(config.system_instruction, str)
    assert "60% fewer words" in config.system_instruction
    assert (
        config.system_instruction.count(UNSLOP_REWRITE_INSTRUCTION)
        + harness.call.prompts[0].count(UNSLOP_REWRITE_INSTRUCTION)
        == 1
    )
    assert "protected_suffix" in harness.call.prompts[0]
    assert '"force_language":["can"]' in harness.call.prompts[0]


@pytest.mark.parametrize("approve", [True, False])
async def test_short_flagged_prose_still_requires_bound_semantic_review(*, approve: bool) -> None:
    source = "At the end of the day, ship."
    replacement = "Ship."
    document = parse_markdown(source, title="Shipping")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[patch])))

    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
    harness.call.response = types.GenerateContentResponse(
        parsed=PatchReviewEnvelope(
            reviews=[
                TextPatchReview(
                    block_id=block.id,
                    expected_sha256=block.sha256,
                    replacement_sha256=hashlib.sha256(replacement.encode()).hexdigest(),
                    equivalent=approve,
                    reason=(
                        "The instruction to ship survives; the editorial transition is removed."
                        if approve
                        else "The phrase sets a deadline; deleting it changes the instruction."
                    ),
                )
            ]
        )
    )

    assert await harness.generator.verify(document, [patch]) == (
        frozenset({block.id}) if approve else frozenset()
    )
    assert len(harness.call.configurations) == 2
    assert harness.call.configurations[-1].system_instruction == UNSLOP_REVIEW_INSTRUCTION
    payload = _ReviewInput.model_validate_json(harness.call.prompts[-1])
    assert len(payload.document_contexts) == 1
    context = payload.document_contexts[0]
    assert context.source_block == source
    assert context.proposed_block == replacement
    assert all(pair.context_id == context.context_id for pair in payload.sentence_audits)
    assert document.blocks[0].text == source


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
    assert harness.call.models == ["gemini-3.1-pro-preview"] * 3


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
    assert harness.call.models == ["gemini-3.8-flash"]
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
        rejected = _RejectedInputs.model_validate_json(_rewrite_payload_lines(prompt)[1]).root[0]
        assert "The draft loses the hidden observer condition." in rejected.review_feedback


async def test_repair_keeps_original_force_and_scope_distinct_from_the_rejected_attempt() -> None:
    source = (
        "Each report can generally remain within the selected folder, "
        "and this discussion takes a circuitous route."
    )
    replacement = "Each report can generally remain within the selected folder."
    document = parse_markdown(source, title="Reports")
    block = document.blocks[0]
    good = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)
    bad = good.model_copy(update={"replacement": "Every report will remain in the folder."})
    unsafe = validate_patches(document, [bad], phase=TransformPhase.UNSLOP, aggressive_unslop=True)
    assert {finding.code for finding in unsafe.findings} == {"force_language_changed"}
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[good])))

    repaired = await harness.generator.repair(document, [bad], unsafe.findings)

    assert repaired == [good]
    arrays = [line for line in harness.call.prompts[0].splitlines() if line.startswith("[")]
    assert len(arrays) == 2
    original = _RewriteInputs.model_validate_json(arrays[0]).root[0]
    rejected = _RejectedInputs.model_validate_json(arrays[1]).root[0]
    assert original.block_id == rejected.block_id == block.id
    assert original.sha256 == block.sha256
    assert original.text == source
    assert original.rewrite_constraints.force_language == ("each", "can")
    assert original.rewrite_constraints.scope_qualifiers == ("generally", "within")
    assert rejected.proposed == bad.replacement
    assert rejected.rejected_constraints.force_language == ("every", "will")
    assert rejected.rejected_constraints.scope_qualifiers == ()
    feedback = {
        note.removeprefix("Exact original constraint counts for ").split(": ", 1)[0]: json.loads(
            note.split(": ", 1)[1]
        )
        for note in rejected.review_feedback
    }
    assert feedback == {
        "force_language": {"expected": {"can": 1, "each": 1}, "proposed": {"every": 1, "will": 1}},
        "scope_qualifiers": {"expected": {"generally": 1, "within": 1}, "proposed": {}},
    }
    safe = validate_patches(document, repaired, phase=TransformPhase.UNSLOP, aggressive_unslop=True)
    assert safe.accepted == {block.id: replacement}
    assert not safe.findings
    assert document.blocks[0].text == source


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


async def test_repair_can_keep_an_unspecified_subject_instead_of_inventing_one() -> None:
    source = "Its song carries through the dense scrub at night."
    document = parse_markdown(source, title="Unspecified subject")
    block = document.blocks[0]
    unsupported = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement="A nightingale's song carries through dense scrub at night.",
    )
    unchanged = unsupported.model_copy(update={"replacement": source})
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[unchanged])))
    critique = "No antecedent names the singer; preserve the unspecified referent."

    assert await harness.generator.repair(
        document,
        [unsupported],
        (TransformFinding(code="semantic_review_rejected", block_id=block.id, detail=critique),),
    ) == [unchanged]
    prompt = harness.call.prompts[0]
    assert harness.call.configurations[0].system_instruction == UNSLOP_REWRITE_INSTRUCTION
    assert UNSLOP_REWRITE_INSTRUCTION not in prompt
    assert source in prompt
    assert unsupported.replacement in prompt
    assert f'"review_feedback":["{critique}"]' in prompt
    assert document.blocks[0].text == source


@pytest.mark.parametrize("failures", [1, 3])
@pytest.mark.parametrize(
    "status",
    [
        HTTPStatus.FORBIDDEN,
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
        None,
    ],
)
async def test_review_failover_uses_the_configured_fallback_within_its_attempt_budget(
    failures: int,
    status: HTTPStatus | None,
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
    harness.call.timeout_failures = status is None
    if status is not None:
        harness.call.failure_status = status
    if failures == 3:
        with pytest.raises(ModelUnavailableError):
            await harness.generator.verify(document, [patch])
    else:
        assert await harness.generator.verify(document, [patch]) == {block.id}
    assert harness.call.models == ["gemini-3.1-pro-preview"] + ["gemini-3.1-flash-lite"] * (
        2 if failures == 3 else 1
    )


@pytest.mark.parametrize("approve", [True, False])
@pytest.mark.parametrize("status", [HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.GATEWAY_TIMEOUT])
async def test_transient_review_fallback_respects_the_bound_verdict(
    status: HTTPStatus, *, approve: bool
) -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    review = TextPatchReview(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement_sha256=hashlib.sha256(REPLACEMENT.encode()).hexdigest(),
        equivalent=approve,
        reason="The fixture either preserves or rejects the bird's hidden singing condition.",
    )
    harness = _generator(
        types.GenerateContentResponse(parsed=PatchReviewEnvelope(reviews=[review]))
    )
    harness.call.failures = 1
    harness.call.failure_status = status

    decision = await harness.generator.review(document, [patch])

    assert decision.approved == (frozenset({block.id}) if approve else frozenset())
    assert set(decision.rejected) == (set() if approve else {block.id})
    assert harness.call.models == ["gemini-3.1-pro-preview", "gemini-3.1-flash-lite"]
    assert all(
        config.system_instruction == UNSLOP_REVIEW_INSTRUCTION
        for config in harness.call.configurations
    )
    assert document.blocks[0].text == SOURCE


@pytest.mark.parametrize("forged_field", ["block_id", "expected_sha256", "replacement_sha256"])
@pytest.mark.parametrize("status", [HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.GATEWAY_TIMEOUT])
async def test_transient_review_fallback_rejects_forged_bindings(
    forged_field: str, status: HTTPStatus
) -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    review = TextPatchReview(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement_sha256=hashlib.sha256(REPLACEMENT.encode()).hexdigest(),
        equivalent=True,
        reason="The fixture claims equivalence with an invalid source or output binding.",
    ).model_copy(update={forged_field: "wrong-block" if forged_field == "block_id" else "0" * 64})
    harness = _generator(
        types.GenerateContentResponse(parsed=PatchReviewEnvelope(reviews=[review]))
    )
    harness.call.failures = 1
    harness.call.failure_status = status

    with pytest.raises(ModelUnavailableError):
        await harness.generator.review(document, [patch])

    assert harness.call.models == [
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.1-flash-lite",
    ]


@pytest.mark.parametrize(
    "status",
    [
        HTTPStatus.BAD_REQUEST,
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.PAYMENT_REQUIRED,
        HTTPStatus.NOT_FOUND,
        HTTPStatus.UNPROCESSABLE_CONTENT,
    ],
)
async def test_permanent_provider_errors_keep_the_configured_reviewer(status: HTTPStatus) -> None:
    document = parse_markdown(SOURCE, title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    harness.call.failures = 1
    harness.call.failure_status = status

    assert (await harness.generator.review(document, [patch])).approved == {block.id}
    assert harness.call.models == ["gemini-3.1-pro-preview"] * 2


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
    submitted = payload.sentence_audits
    assert len(submitted) == 6
    assert len(payload.document_contexts) == 1
    context = payload.document_contexts[0]
    assert context.source_block == paragraph
    assert context.proposed_block == REPLACEMENT
    assert all(
        pair.source_sentence == SOURCE and pair.context_id == context.context_id
        for pair in submitted
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
    assert all(len(batch.sentence_audits) <= 12 for batch in submitted)
    assert all(len(prompt) <= 12_000 for prompt in harness.call.prompts)
    assert [pair.block_id for batch in submitted for pair in batch.sentence_audits] == [
        patch.block_id for patch in patches
    ]
    assert harness.call.models == ["gemini-3.1-pro-preview"] * len(submitted)


async def test_sre_review_batching_reduces_requests_with_identical_complete_audits() -> None:
    document = parse_markdown("\n\n".join(_SRE_REVIEW_PARAGRAPHS), title="Latency reference")
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)
        for block in model_editable_blocks(document)
    ]
    plan = transform_models._review_plan(document, {patch.block_id: patch for patch in patches})
    pairs = [unit.pair for unit in plan.units]
    wide = transform_models._review_batches(pairs)
    narrow = transform_models._review_batches(pairs, maximum_characters=8_000)
    wide_prompts = [transform_models._review_prompt(batch) for batch in wide]
    narrow_prompts = [transform_models._review_prompt(batch) for batch in narrow]

    def bindings(prompts: list[str]) -> list[str]:
        result: list[str] = []
        for prompt in prompts:
            payload = _ReviewInput.model_validate_json(prompt)
            contexts = {context.context_id: context for context in payload.document_contexts}
            result.extend(
                json.dumps(
                    {
                        "audit": audit.model_dump(exclude={"context_id"}),
                        "context": contexts[audit.context_id].model_dump(
                            mode="json", exclude={"context_id"}
                        ),
                    },
                    sort_keys=True,
                )
                for audit in payload.sentence_audits
            )
        return result

    assert len(wide) < len(narrow)
    assert bindings(wide_prompts) == bindings(narrow_prompts)
    assert len(bindings(wide_prompts)) == len(set(bindings(wide_prompts)))
    assert all(len(prompt) <= 12_000 for prompt in wide_prompts)
    assert all(len(batch) <= 12 for batch in wide)

    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    decision = await harness.generator.review(document, patches)
    assert harness.call.prompts == wide_prompts
    assert decision.approved | decision.rejected.keys() == {patch.block_id for patch in patches}
    assert all(config.max_output_tokens == 32_768 for config in harness.call.configurations)


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
    submitted = payload.sentence_audits
    assert len(submitted) == 2
    assert {pair.source_sentence for pair in submitted} == {
        "Its song belongs within a breeding season, a territory and an annual journey.",
        provenance,
    }
    assert len(payload.document_contexts) == 1
    context = payload.document_contexts[0]
    assert context.source_block == source
    assert context.proposed_block == patch.replacement
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
        submitted = _ReviewInput.model_validate_json(harness.call.prompts[0]).sentence_audits
        hashes.append(
            next(pair.expected_sha256 for pair in submitted if pair.source_sentence == shared)
        )
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
    assert sum(len(payload.sentence_audits) for payload in payloads) == 60
    assert len(payloads) <= 6
    assert all(0 < len(payload.sentence_audits) <= 12 for payload in payloads)
    assert all(len(prompt) <= 12_000 for prompt in harness.call.prompts)
    for payload in payloads:
        contexts = {context.context_id: context for context in payload.document_contexts}
        assert len(contexts) == len(payload.document_contexts)
        assert len(contexts) < len(payload.sentence_audits)
        assert all(
            contexts[pair.context_id].source_block in paragraphs for pair in payload.sentence_audits
        )
        assert all(context.proposed_block == REPLACEMENT for context in contexts.values())


@pytest.mark.parametrize(
    "prefix",
    [
        "Put another way, ",
        "To put it another way: ",
        "In other words, ",
        "Put another way, and to explain the same idea from another angle, ",
        "The same point bears repeating in simpler language: ",
    ],
)
def test_conventional_framing_can_go_without_relaxing_the_following_obligation(prefix: str) -> None:
    fact = "every reviewer must inspect the same region as every other reviewer."
    proposed = "Every reviewer must inspect the same region as every other reviewer."
    source = prefix + fact
    masked = without_editorial_framing(source)
    assert len(masked) == len(source)
    assert not masked[: len(prefix)].strip()
    assert masked[len(prefix) :] == fact
    constraints = rewrite_constraints(source)
    assert constraints.force_language == ("every", "must", "every")
    assert constraints.scope_qualifiers == ("same", "other")
    document = parse_markdown(source, title="Reviews")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=proposed)
    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert decision.accepted == {block.id: proposed}
    assert not decision.findings
    assert UNSLOP_POLICY_VERSION == "sarj-concise-v20"


@pytest.mark.parametrize(
    "source",
    [
        "Write it in other words; every reviewer must preserve the same requirement.",
        "To explain the same idea from another angle, writers must preserve every qualification.",
        "In other words writers must preserve every qualification.",
        'The rule is "In other words, every reviewer must inspect another region."',
        "The rule is `Put another way, every sample is from another region.`",
        "[1. In other words, every reviewer must inspect another region.]",
        "Read https://example.com/another?other=same before choosing another region.",
        "Taken together, these observations may support another explanation.",
        "There is no mystery about why another option may work.",
        "The same point bears repeating material loads.",
        "Put another way writers must preserve every qualification.",
    ],
)
def test_factual_normative_and_protected_uses_are_not_editorial_masks(source: str) -> None:
    assert without_editorial_framing(source) == source


def test_framing_removal_cannot_drop_factual_quantifiers_negation_or_comparisons() -> None:
    source = "In other words, every sample must use the same region and no other region."
    document = parse_markdown(source, title="Sampling")
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement="Every sample must use the same region.",
    )
    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert not decision.accepted
    assert {finding.code for finding in decision.findings} == {"force_language_changed"}
    assert rewrite_constraints(source).force_language == ("every", "must", "no")


@pytest.mark.parametrize(
    ("source", "clauses"),
    [
        (
            "Atlas condenses prose; Prism refines layouts for readers.",
            ("Atlas condenses prose", "Prism refines layouts for readers."),
        ),
        (
            "Atlas condenses prose, whereas Prism refines layouts for readers.",
            ("Atlas condenses prose", "Prism refines layouts for readers."),
        ),
        (
            "The suite includes Atlas to condense prose and Prism to refine layouts for readers.",
            ("Atlas to condense prose", "Prism to refine layouts for readers."),
        ),
        (
            "Each sensor must record every reading; the pump can run only after approval.",
            (
                "Each sensor must record every reading",
                "the pump can run only after approval.",
            ),
        ),
        (
            "Atlas condenses prose [12]; Prism refines layouts for readers [13].",
            ("Atlas condenses prose [12]", "Prism refines layouts for readers [13]."),
        ),
    ],
)
async def test_independent_clause_audits_add_exact_spans_without_replacing_the_sentence(
    source: str, clauses: tuple[str, str]
) -> None:
    document = parse_markdown(source, title="Features")
    block = document.blocks[0]
    proposed = source
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=proposed)
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    assert await harness.generator.verify(document, [patch]) == frozenset({block.id})
    payload = _ReviewInput.model_validate_json(harness.call.prompts[0])
    original, *children = payload.sentence_audits
    assert original.source_kind == "sentence"
    assert original.source_span is None
    assert original.parent_sentence is None
    assert original.block_id == block.id
    assert original.expected_sha256 == block.sha256
    assert original.source_sentence == source
    assert tuple(child.source_sentence for child in children) == clauses
    for child in children:
        assert child.source_kind == "clause"
        assert child.source_span is not None
        start, end = child.source_span
        assert source[start:end] == child.source_sentence
        assert child.parent_sentence == source
        identity = [block.sha256, 0, "clause", source, start, end, child.source_sentence]
        assert (
            child.expected_sha256
            == hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
        )
        assert child.replacement_sha256 == hashlib.sha256(proposed.encode()).hexdigest()
        assert child.context_id == original.context_id
    assert payload.document_contexts[0].source_block == source
    assert payload.document_contexts[0].proposed_block == proposed


@pytest.mark.parametrize("verdict", ["reject", "omit"])
@pytest.mark.parametrize(
    ("source", "missing_function"),
    [
        (
            "Atlas condenses prose; Prism refines layouts for readers.",
            "Prism refines layouts for readers.",
        ),
        (
            "Atlas condenses prose while Prism refines layouts for readers.",
            "while Prism refines layouts for readers.",
        ),
    ],
)
async def test_a_child_clause_cannot_be_overruled_by_a_whole_sentence_approval(
    verdict: str, source: str, missing_function: str
) -> None:
    document = parse_markdown(source, title="Features")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    if verdict == "omit":
        harness.call.omitted_sources.add(missing_function)
        with pytest.raises(ModelUnavailableError):
            await harness.generator.review(document, [patch])
        return
    harness.call.rejected_sources.add(missing_function)
    decision = await harness.generator.review(document, [patch])
    assert not decision.approved
    assert set(decision.rejected) == {block.id}


@pytest.mark.parametrize(
    ("source", "focus"),
    [
        (
            "Cleaner is intended to tighten prose, while Formatter improves presentation.",
            "while Formatter improves presentation.",
        ),
        (
            "Recorder collects data from sensors while Pump runs.",
            "while Pump runs.",
        ),
        (
            "Recorder collects data from sensors while Pump may not run.",
            "while Pump may not run.",
        ),
        (
            "While Pump runs, Recorder collects data from sensors.",
            "While Pump runs",
        ),
    ],
)
async def test_while_focus_retains_its_conjunction_predicate_and_exact_parent_context(
    source: str, focus: str
) -> None:
    document = parse_markdown(source, title="Contextual relationships")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    assert await harness.generator.verify(document, [patch]) == frozenset({block.id})
    payload = _ReviewInput.model_validate_json(harness.call.prompts[0])
    original, child = payload.sentence_audits
    assert original.block_id == block.id
    assert original.expected_sha256 == block.sha256
    assert original.source_kind == "sentence"
    assert child.source_kind == "dependent_clause"
    assert child.source_sentence == focus
    assert child.source_span is not None
    start, end = child.source_span
    assert source[start:end] == focus
    assert child.parent_sentence == source
    identity = [block.sha256, 0, "dependent_clause", source, start, end, focus]
    assert (
        child.expected_sha256
        == hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    )
    assert child.replacement_sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert child.context_id == original.context_id
    assert payload.document_contexts[0].source_block == source


async def test_dependent_focus_binds_its_exact_parent_among_other_original_sentences() -> None:
    parent = "Recorder collects data from sensors while Pump runs."
    source = "Atlas condenses prose. " + parent
    document = parse_markdown(source, title="Parent binding")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    assert await harness.generator.verify(document, [patch]) == frozenset({block.id})
    payload = _ReviewInput.model_validate_json(harness.call.prompts[0])
    sentences = [audit for audit in payload.sentence_audits if audit.source_kind == "sentence"]
    assert [audit.source_sentence for audit in sentences] == ["Atlas condenses prose.", parent]
    focus = next(
        audit for audit in payload.sentence_audits if audit.source_kind == "dependent_clause"
    )
    assert focus.parent_sentence == parent
    assert focus.source_span is not None
    start, end = focus.source_span
    assert source[start:end] == "while Pump runs."
    identity = [block.sha256, 1, "dependent_clause", parent, start, end, focus.source_sentence]
    assert (
        focus.expected_sha256
        == hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    )
    assert payload.document_contexts[0].source_block == source


@pytest.mark.parametrize(
    "source",
    [
        "If Atlas condenses prose, Prism refines layouts; Sensor records measurements.",
        "The suite previews documents, spreadsheets, presentations and PDF files.",
        "Atlas condenses prose and Prism refines layouts for readers.",
        'The manual says "Atlas condenses prose; Prism refines layouts" as an example.',
        "The manual prints `Atlas condenses prose; Prism refines layouts` as an example.",
        "Read https://example.com/Atlas;Prism/refines before carefully reviewing the full source example.",
        "The citation [Atlas condenses prose; Prism refines layouts] describes both features.",
        'The quote reads "A feature exists. Atlas condenses prose; Prism refines layouts."',
        "For a while Atlas condenses prose and Prism refines layouts for readers.",
        'The quote reads "Atlas condenses prose while Prism refines layouts for readers."',
        'Recorder collects data while Pump displays "A. B" on the screen.',
        "Atlas condenses prose (using context (from records); Prism refines layouts).",
        "Atlas condenses prose [using context; Prism refines layouts for readers.",
    ],
)
async def test_ambiguous_coordination_lists_and_protected_delimiters_keep_whole_audits(
    source: str,
) -> None:
    document = parse_markdown(source, title="Boundaries")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    assert await harness.generator.verify(document, [patch]) == frozenset({block.id})
    submitted = [
        pair
        for prompt in harness.call.prompts
        for pair in _ReviewInput.model_validate_json(prompt).sentence_audits
    ]
    assert submitted
    assert all(pair.source_kind == "sentence" and pair.source_span is None for pair in submitted)


@pytest.mark.parametrize(
    "source",
    [
        (
            "Atlas condenses prose; Prism refines layouts; Sensor records measurements; "
            "Pump controls flow; Valve regulates pressure."
        ),
        (
            "Recorder collects data from sensors while Pump runs while Sensor records data "
            "while Valve controls flow while Motor operates while Timer records intervals."
        ),
    ],
)
async def test_clause_budget_overflow_withholds_the_block_and_retains_original_sentence_audits(
    source: str,
) -> None:
    document = parse_markdown(source, title="Coverage")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    decision = await harness.generator.review(document, [patch])
    assert not decision.approved
    assert "bounded review budget" in decision.rejected[block.id]
    submitted = _ReviewInput.model_validate_json(harness.call.prompts[0]).sentence_audits
    assert [(pair.block_id, pair.source_kind, pair.source_sentence) for pair in submitted] == [
        (block.id, "sentence", source)
    ]


async def test_document_clause_budget_overflow_does_not_silently_approve_a_later_block() -> None:
    source = "Atlas condenses prose; Prism refines layouts for readers."
    document = parse_markdown("\n\n".join([source] * 17), title="Coverage")
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
        for block in document.blocks
        if block.text.strip()
    ]
    harness = _generator(types.GenerateContentResponse())
    harness.call.review_input = True
    decision = await harness.generator.review(document, patches)
    last = patches[-1].block_id
    assert decision.approved == frozenset(patch.block_id for patch in patches[:-1])
    assert set(decision.rejected) == {last}
    assert "bounded review budget" in decision.rejected[last]
    submitted = [
        pair
        for prompt in harness.call.prompts
        for pair in _ReviewInput.model_validate_json(prompt).sentence_audits
    ]
    assert {pair.block_id for pair in submitted if pair.source_kind == "sentence"} == {
        patch.block_id for patch in patches
    }
    assert sum(pair.source_kind == "clause" for pair in submitted) == 32


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize(
    ("heading", "replacement"),
    [
        ("Storage overview", "Storage"),
        ("A few thoughts about the storage interface", "Storage interface"),
    ],
)
async def test_short_heading_uses_trusted_role_and_bound_safety_path(
    heading: str, replacement: str, *, native: bool
) -> None:
    document = (
        parse_google_document(
            {
                "revisionId": "fixture-revision",
                "tabs": [
                    {
                        "tabProperties": {"tabId": "tab-one"},
                        "documentTab": {
                            "body": {
                                "content": [
                                    {
                                        "startIndex": 1,
                                        "endIndex": len(heading) + 2,
                                        "paragraph": {
                                            "paragraphStyle": {"namedStyleType": "HEADING_2"},
                                            "elements": [{"textRun": {"content": heading + "\n"}}],
                                        },
                                    }
                                ]
                            }
                        },
                    }
                ],
            },
            title="Storage",
        )
        if native
        else parse_markdown("## " + heading, title="Storage")
    )
    block = document.blocks[0]
    assert block.kind is BlockKind.HEADING
    assert block.editable
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[patch])))
    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
    supplied = _RewriteInputs.model_validate_json(
        next(line for line in harness.call.prompts[0].splitlines() if line.startswith("["))
    ).root
    assert len(supplied) == 1
    assert supplied[0].kind is BlockKind.HEADING
    assert supplied[0].sha256 == block.sha256
    harness.call.response = types.GenerateContentResponse(
        parsed=PatchReviewEnvelope(
            reviews=[
                TextPatchReview(
                    block_id=block.id,
                    expected_sha256=block.sha256,
                    replacement_sha256=hashlib.sha256(replacement.encode()).hexdigest(),
                    equivalent=True,
                    reason="The fixture retains the storage topic and removes only heading padding.",
                )
            ]
        )
    )
    assert (await harness.generator.review(document, [patch])).approved == {block.id}
    context = _ReviewInput.model_validate_json(harness.call.prompts[-1]).document_contexts[0]
    assert context.block_kind is BlockKind.HEADING
    assert (context.source_block, context.proposed_block) == (heading, replacement)
    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert decision.accepted == {block.id: replacement}
    assert not decision.findings


async def test_concise_heading_stays_unchanged_and_short_body_stays_out_of_scope() -> None:
    heading = parse_markdown("## Storage", title="Storage")
    block = heading.blocks[0]
    unchanged = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[unchanged])))
    assert await harness.generator.generate(heading, phase=TransformPhase.UNSLOP) == [unchanged]
    body = parse_markdown("Storage overview", title="Storage")
    before = len(harness.call.prompts)
    assert await harness.generator.generate(body, phase=TransformPhase.UNSLOP) == []
    assert len(harness.call.prompts) == before


@pytest.mark.parametrize(
    ("heading", "replacement", "code"),
    [
        ("About Atlas", "About Prism", "proper_noun_changed"),
        ("Version 3 summary", "Version 4 summary", "protected_content_changed"),
        ('Read "source values"', 'Read "values"', "quoted_content_changed"),
        ("Service may start", "Service will start", "force_language_changed"),
    ],
)
def test_heading_scope_preserves_existing_fact_and_literal_guards(
    heading: str, replacement: str, code: str
) -> None:
    document = parse_markdown("## " + heading, title="Source")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not decision.accepted
    assert {finding.code for finding in decision.findings} == {code}


async def test_short_native_linked_heading_keeps_existing_noneditable_boundary() -> None:
    heading = "Storage overview"
    document = parse_google_document(
        {
            "revisionId": "fixture-revision",
            "tabs": [
                {
                    "tabProperties": {"tabId": "tab-one"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 1,
                                    "endIndex": len(heading) + 2,
                                    "paragraph": {
                                        "paragraphStyle": {"namedStyleType": "HEADING_2"},
                                        "elements": [
                                            {
                                                "textRun": {
                                                    "content": heading + "\n",
                                                    "textStyle": {
                                                        "link": {
                                                            "url": "https://example.com/storage"
                                                        }
                                                    },
                                                }
                                            }
                                        ],
                                    },
                                }
                            ]
                        }
                    },
                }
            ],
        },
        title="Storage",
    )
    assert document.blocks[0].kind is BlockKind.HEADING
    assert document.blocks[0].editable is False
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[])))
    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == []
    assert not harness.call.prompts


@pytest.mark.parametrize("repetitions", [1, 25])
async def test_smaller_editor_requests_preserve_all_blocks_hashes_and_order(
    repetitions: int,
) -> None:
    source = " ".join([SOURCE] * repetitions)
    document = parse_markdown("\n\n".join(source for _ in range(13)), title="Editor batches")
    blocks = [block for block in document.blocks if block.text]
    harness = _generator(types.GenerateContentResponse())
    harness.call.rewrite_input = True
    patches = await harness.generator.generate(document, phase=TransformPhase.UNSLOP)
    submitted = [
        _RewriteInputs.model_validate_json(_rewrite_payload_lines(prompt)[0]).root
        for prompt in harness.call.prompts
    ]
    assert all(0 < len(batch) <= 6 for batch in submitted)
    assert all(sum(len(block.text) for block in batch) <= 4_000 for batch in submitted)
    assert [block.block_id for batch in submitted for block in batch] == [
        block.id for block in blocks
    ]
    assert [(patch.block_id, patch.expected_sha256, patch.replacement) for patch in patches] == [
        (block.id, block.sha256, block.text) for block in blocks
    ]
    assert [len(batch) for batch in submitted] == (
        [6, 6, 1] if repetitions == 1 else [1] * len(blocks)
    )


async def test_single_oversized_block_and_audit_are_preserved_whole() -> None:
    source = "The specification describes " + "storage " * 600 + "behavior."
    document = parse_markdown(source, title="Large source block")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[patch])))
    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
    supplied = _RewriteInputs.model_validate_json(
        next(line for line in harness.call.prompts[0].splitlines() if line.startswith("["))
    ).root
    assert len(supplied) == 1
    assert supplied[0].text == source
    assert len(source) > 4_000
    harness.call.review_input = True
    assert (await harness.generator.review(document, [patch])).approved == {block.id}
    payload = _ReviewInput.model_validate_json(harness.call.prompts[-1])
    assert len(harness.call.prompts) == 2
    assert len(payload.sentence_audits) == 1
    assert (
        payload.document_contexts[0].source_block
        == payload.document_contexts[0].proposed_block
        == source
    )
    assert payload.sentence_audits[0].expected_sha256 == block.sha256
    assert len(harness.call.prompts[-1]) > 8_000


async def test_parallel_repair_keeps_feedback_bound_to_each_current_batch() -> None:
    document = parse_markdown("\n\n".join(SOURCE for _ in range(13)), title="Repair batches")
    blocks = [block for block in document.blocks if block.text]
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=REPLACEMENT)
        for block in blocks
    ]
    findings = tuple(
        TransformFinding(
            code="semantic_review_rejected", block_id=block.id, detail="Review " + block.id
        )
        for block in blocks
    )
    harness = _generator(types.GenerateContentResponse())
    harness.call.rewrite_input = True
    repaired = await harness.generator.repair(document, patches, findings)
    assert [(patch.block_id, patch.expected_sha256, patch.replacement) for patch in repaired] == [
        (block.id, block.sha256, block.text) for block in blocks
    ]
    for prompt in harness.call.prompts:
        arrays = _rewrite_payload_lines(prompt)
        current = _RewriteInputs.model_validate_json(arrays[0]).root
        rejected = _RejectedInputs.model_validate_json(arrays[1]).root
        assert [block.block_id for block in current] == [block.block_id for block in rejected]
        assert all(item.proposed == REPLACEMENT for item in rejected)
        assert all(f'"review_feedback":["Review {block.block_id}"]' in prompt for block in current)


def _rewrite_payload_lines(prompt: str) -> list[str]:
    return [line for line in prompt.splitlines() if line.startswith("[")]


async def test_numeric_concision_target_and_failure_feedback_remain_source_bound() -> None:
    source = (
        "The sensor may record temperature at night."
        + (
            " The explanation describes the discussion in expanded wording before returning to the description."
        )
        * 4
    )
    document = parse_markdown(source, title="Sensor")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[patch])))

    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
    initial = _RewriteInputs.model_validate_json(
        _rewrite_payload_lines(harness.call.prompts[0])[0]
    ).root[0]
    assert initial.concision_target is not None
    assert initial.concision_target.source_words == 59
    assert initial.concision_target.max_words == 24
    assert initial.concision_target.draft_target_words == 12
    assert initial.sha256 == block.sha256
    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert not decision.accepted
    assert not decision.findings

    result = await TextTransformPlanner(
        max_editorial_passes=1, generator=harness.generator, allow_unslop_model=True
    ).transform(
        document,
        DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content=source,
                name="Sensor",
                destination_folder_id=FileId("folder"),
            ),
            mode=TransformMode.UNSLOP,
        ),
    )

    assert any(finding.code == "concision_target_unmet" for finding in result.findings)
    arrays = _rewrite_payload_lines(harness.call.prompts[-1])
    current = _RewriteInputs.model_validate_json(arrays[0]).root[0]
    rejected = _RejectedInputs.model_validate_json(arrays[1]).root[0]
    assert current == initial
    assert rejected.proposed == source
    assert len(rejected.review_feedback) == 1
    assert "proposal 59 words" in rejected.review_feedback[0]
    assert "maximum 23" in rejected.review_feedback[0]
    assert "Local block targets are advisory" in rejected.review_feedback[0]


async def test_repair_feedback_preserves_an_empty_original_force_counter() -> None:
    source = "The sensor records readings from the control unit at night."
    document = parse_markdown(source, title="Sensor readings")
    block = document.blocks[0]
    good = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    bad = good.model_copy(update={"replacement": source.replace("records", "can record")})
    decision = validate_patches(
        document, [bad], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[good])))

    assert await harness.generator.repair(document, [bad], decision.findings) == [good]

    arrays = _rewrite_payload_lines(harness.call.prompts[0])
    original = _RewriteInputs.model_validate_json(arrays[0]).root[0]
    rejected = _RejectedInputs.model_validate_json(arrays[1]).root[0]
    assert original.sha256 == good.expected_sha256 == block.sha256
    assert original.rewrite_constraints.force_language == ()
    assert rejected.rejected_constraints.force_language == ("can",)
    assert rejected.review_feedback == [
        'Exact original constraint counts for force_language: {"expected":{},"proposed":{"can":1}}'
    ]
    assert original.text == source


@pytest.mark.parametrize(
    ("source_words", "maximum", "draft"),
    [
        pytest.param(94, 38, 19, id="near-cap-purpose"),
        pytest.param(103, 42, 21, id="near-cap-alternatives"),
    ],
)
@pytest.mark.parametrize("suffix", ["", " [7]"], ids=["plain", "native-citation"])
async def test_writer_draft_margin_stays_below_the_unchanged_suffix_excluded_cap(
    source_words: int, maximum: int, draft: int, suffix: str
) -> None:
    source = " ".join("reading" for _ in range(source_words))
    document = parse_markdown(source, title="Draft margin")
    block = replace(document.blocks[0], text=source + suffix, protected_suffix=suffix or None)
    document = replace(document, blocks=(block,))
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[patch])))

    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]

    submitted = _RewriteInputs.model_validate_json(
        _rewrite_payload_lines(harness.call.prompts[0])[0]
    ).root[0]
    assert submitted.text == block.text
    assert submitted.sha256 == block.sha256
    assert submitted.concision_target == _ConcisionInput(
        source_words=source_words, max_words=maximum, draft_target_words=draft
    )
    assert draft < maximum < source_words
    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert not decision.findings


@pytest.mark.parametrize(
    ("source", "achieved", "qualifier"),
    [
        pytest.param(
            "The team identified an opportunity to reduce waste.",
            "The team reduced waste.",
            "opportunity to",
            id="identified-opportunity-is-not-achievement",
        ),
        pytest.param(
            "The audit identified opportunities to reduce inspection delays.",
            "The audit reduced inspection delays.",
            "opportunities to",
            id="plural-opportunities-are-not-achievement",
        ),
    ],
)
def test_opportunity_goal_cannot_be_promoted_to_an_achieved_result(
    source: str, achieved: str, qualifier: str
) -> None:
    document = parse_markdown(source, title="Goal qualification")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=achieved)

    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )

    assert rewrite_constraints(source).force_language == (qualifier,)
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]
    assert not decision.accepted


def test_goal_preserving_rewrite_normalizes_opportunity_phrase_spacing() -> None:
    source = "The team identified an opportunity   to reduce waste."
    replacement = "The team found an opportunity to reduce waste."
    document = parse_markdown(source, title="Goal spacing")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)

    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )

    assert rewrite_constraints(source).force_language == ("opportunity to",)
    assert rewrite_constraints(replacement).force_language == ("opportunity to",)
    assert decision.accepted == {block.id: replacement}
    assert not decision.findings


@pytest.mark.parametrize(
    "fact",
    [
        pytest.param("The audit records opportunity cost.", id="opportunity-cost-is-not-a-goal"),
        pytest.param(
            "The sensor measures potential energy.", id="physical-potential-is-not-a-goal"
        ),
    ],
)
def test_non_goal_nouns_allow_removing_fact_free_repetition(fact: str) -> None:
    source = fact + " This explanation repeats the point at length without adding information."
    document = parse_markdown(source, title="Noun distinctions")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=fact)

    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )

    assert rewrite_constraints(source).force_language == ()
    assert decision.accepted == {block.id: fact}
    assert not decision.findings


async def test_opportunity_loss_repair_receives_original_goal_counter_at_high_effort() -> None:
    source = "The audit identified an opportunity to reduce errors in the process."
    replacement = "The audit found an opportunity to reduce errors in the process."
    document = parse_markdown(source, title="Goal repair")
    block = document.blocks[0]
    bad = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement="The audit reduced errors in the process.",
    )
    good = bad.model_copy(update={"replacement": replacement})
    decision = validate_patches(
        document, [bad], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[good])))

    assert await harness.generator.repair(document, [bad], decision.findings) == [good]

    original_payload, rejected_payload = _rewrite_payload_lines(harness.call.prompts[0])
    original = _RewriteInputs.model_validate_json(original_payload).root[0]
    rejected = _RejectedInputs.model_validate_json(rejected_payload).root[0]
    assert original.sha256 == block.sha256
    assert original.rewrite_constraints.force_language == ("opportunity to",)
    assert rejected.rejected_constraints.force_language == ()
    assert rejected.review_feedback == [
        (
            "Exact original constraint counts for force_language: "
            '{"expected":{"opportunity to":1},"proposed":{}}'
        )
    ]
    config = harness.call.configurations[0]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_level is types.ThinkingLevel.HIGH


@pytest.mark.parametrize(
    "prefix",
    [
        "In today’s increasingly fast-moving and ever-evolving world of ",
        "In today's fast-moving and ever-evolving world of ",
    ],
    ids=["smart-quote-long-form", "ascii-quote-short-form"],
)
def test_stock_world_opening_does_not_bind_its_rhetorical_compound(prefix: str) -> None:
    topic = "fieldwork, "
    fact = "each sensor may record the available day-to-day measurements."
    source = prefix + topic + fact

    projected = without_editorial_framing(source)
    constraints = rewrite_constraints(source)

    assert len(projected) == len(source)
    assert not projected[: len(prefix)].strip()
    assert projected[len(prefix) :] == topic + fact
    assert constraints.force_language == ("each", "may")
    assert constraints.scope_qualifiers == ("available", "day-to-day")


@pytest.mark.parametrize(
    "source",
    [
        "Fast-moving water carries sediment past the sensor.",
        "In today’s increasingly fast-moving water, each sensor may record a measurement.",
        "In today’s fast-moving and ever-evolving world of fieldwork each sensor may record.",
        '"In today’s fast-moving and ever-evolving world of fieldwork," is the exact quote.',
        "`In today’s fast-moving and ever-evolving world of fieldwork,` is the exact code.",
    ],
    ids=["empirical-water", "empirical-condition", "no-comma-boundary", "quotation", "code"],
)
def test_empirical_incomplete_and_protected_fast_moving_phrases_stay_unmasked(
    source: str,
) -> None:
    assert without_editorial_framing(source) == source
    assert "fast-moving" in rewrite_constraints(source).scope_qualifiers


@pytest.mark.parametrize(
    ("source_words", "proposed_words", "maximum"),
    [
        pytest.param(111, 47, 45, id="actual-v17-near-cap-goal"),
        pytest.param(103, 49, 42, id="actual-v17-near-cap-alternatives"),
    ],
)
def test_reported_near_cap_proposals_reach_review_with_advisory_block_allocations(
    source_words: int, proposed_words: int, maximum: int
) -> None:
    document = parse_markdown(" ".join(["reading"] * source_words), title="Hard cap")
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement=" ".join(["reading"] * proposed_words),
    )

    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )

    target = concision_target(block)
    assert target is not None
    assert target.source_words == source_words
    assert target.max_words == maximum < proposed_words
    assert decision.accepted == {block.id: patch.replacement}
    assert not decision.findings


def test_stock_opening_preserves_the_topic_clause_force_and_scope() -> None:
    prefix = "In today’s fast-moving and ever-evolving world of "
    topic = "medicine where each nurse must record every dose from the same population, "
    fact = "a sensor may record measurements."
    source = prefix + topic + fact

    projected = without_editorial_framing(source)
    constraints = rewrite_constraints(source)

    assert not projected[: len(prefix)].strip()
    assert projected[len(prefix) :] == topic + fact
    assert constraints.force_language == ("each", "must", "every", "may")
    assert constraints.scope_qualifiers == ("same",)


def test_stock_opening_preserves_a_concrete_fast_moving_topic() -> None:
    prefix = "In today’s fast-moving and ever-evolving world of "
    topic = "fast-moving water, "
    fact = "each sensor may record measurements."
    source = prefix + topic + fact

    projected = without_editorial_framing(source)
    constraints = rewrite_constraints(source)

    assert not projected[: len(prefix)].strip()
    assert projected[len(prefix) :] == topic + fact
    assert constraints.force_language == ("each", "may")
    assert constraints.scope_qualifiers == ("fast-moving",)


@pytest.mark.parametrize(
    ("source", "rejected", "expected_tokens", "proposed_tokens"),
    [
        pytest.param(
            "Multiply 15 by 11.28 gives 169.2 ms; record 30 requests and 30 requests.",
            "Multiply 15 times 11.28 yields 169.2 ms; record 30 requests.",
            {"15 by": 1, "11.28 gives": 1, "169.2 ms": 1, "30": 2},
            {"15 times": 1, "11.28": 1, "169.2 ms": 1, "30": 1},
            id="whole-quantity-tokens-and-repeated-occurrences",
        ),
        pytest.param(
            "The sensor records readings from the control unit at night.",
            "The sensor records readings for 15 ms at night.",
            {},
            {"15 ms": 1},
            id="empty-original-protected-counter",
        ),
    ],
)
async def test_repair_reports_exact_original_and_proposed_protected_token_counts(
    source: str,
    rejected: str,
    expected_tokens: dict[str, int],
    proposed_tokens: dict[str, int],
) -> None:
    document = parse_markdown(source, title="Protected token repair")
    block = document.blocks[0]
    bad = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=rejected)
    good = bad.model_copy(update={"replacement": source})
    unsafe = validate_patches(document, [bad], phase=TransformPhase.UNSLOP, aggressive_unslop=True)
    assert [finding.code for finding in unsafe.findings] == ["protected_content_changed"]
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=[good])))

    assert await harness.generator.repair(document, [bad], unsafe.findings) == [good]

    current, previous = _rewrite_payload_lines(harness.call.prompts[0])
    original = _RewriteInputs.model_validate_json(current).root[0]
    failed = _RejectedInputs.model_validate_json(previous).root[0]
    prefix = "Exact original constraint counts for protected_tokens: "
    token_feedback = [
        json.loads(note.removeprefix(prefix))
        for note in failed.review_feedback
        if note.startswith(prefix)
    ]
    assert token_feedback == [{"expected": expected_tokens, "proposed": proposed_tokens}]
    assert original.text == source
    assert original.sha256 == good.expected_sha256 == block.sha256
    assert failed.proposed == rejected
    assert not validate_patches(
        document, [good], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    ).findings
    assert document.blocks[0].text == source


async def test_writer_supplies_numeric_advisory_batch_draft_and_reports_global_overage() -> None:
    paragraph = " ".join("reading" for _ in range(60))
    source = paragraph + "\n\n" + paragraph
    document = parse_markdown(source, title="Batch word allocation")
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)
        for block in model_editable_blocks(document)
    ]
    harness = _generator(types.GenerateContentResponse(parsed=PatchEnvelope(patches=patches)))

    assert await harness.generator.generate(document, phase=TransformPhase.UNSLOP) == patches

    prompt = harness.call.prompts[0]
    assert "120 eligible prose words in these supplied blocks" in prompt
    assert "combined advisory draft allocation is 24 words" in prompt
    assert "a dense block may exceed its allocation" in prompt
    result = await TextTransformPlanner(
        max_editorial_passes=1, generator=harness.generator, allow_unslop_model=True
    ).transform(
        document,
        DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                content=source,
                name="Batch word allocation",
                destination_folder_id=FileId("folder"),
            ),
            mode=TransformMode.UNSLOP,
        ),
    )

    assert any(finding.code == "concision_target_unmet" for finding in result.findings)
