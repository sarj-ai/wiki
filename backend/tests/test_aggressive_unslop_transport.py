from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
from http import HTTPStatus
import json
import re
from typing import Literal

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
from pydantic import BaseModel, ConfigDict, Field
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import parse_markdown
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    PatchEnvelope,
    PatchReviewEnvelope,
    TextPatch,
    TextPatchReview,
    TransformPhase,
)


class _VendorRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    generation_config: types.GenerateContentConfig = Field(alias="generationConfig")


class _WirePair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str
    expected_sha256: str
    replacement_sha256: str
    source_sentence: str
    context_id: str
    source_kind: Literal["sentence", "clause", "dependent_clause"]
    source_span: tuple[int, int] | None
    parent_sentence: str | None


class _WireContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context_id: str
    source_block: str
    proposed_block: str


class _WireReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_contexts: list[_WireContext]
    sentence_audits: list[_WirePair]


class _WireText(BaseModel):
    text: str


class _WireContent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    parts: list[_WireText]


class _WireReviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    contents: list[_WireContent]


_FEATURE_RELATION = re.compile(
    r"\b(Atlas|Prism|Cleaner|Formatter)\s+(?:(is intended to|to)\s+)?"
    r"(condenses?|refines?|tightens?|improves?)\s+(prose|layouts|presentation)\b"
)
_TEMPORAL_PREDICATE = r"(?:Recorder collects data from sensors|Pump (?:runs|may not run))"
_TEMPORAL_POSTFIX = re.compile(
    rf"(?P<main>{_TEMPORAL_PREDICATE})\s+while\s+(?P<condition>{_TEMPORAL_PREDICATE})",
    re.IGNORECASE,
)
_TEMPORAL_PREFIX = re.compile(
    rf"\bwhile\s+(?P<condition>{_TEMPORAL_PREDICATE}),\s+(?P<main>{_TEMPORAL_PREDICATE})",
    re.IGNORECASE,
)


def _feature_relations(text: str) -> frozenset[tuple[str, str, str, str]]:
    return frozenset(
        (
            match.group(1),
            "intended" if match.group(2) == "is intended to" else "function",
            match.group(3).removesuffix("s"),
            match.group(4),
        )
        for match in _FEATURE_RELATION.finditer(text)
    )


def _temporal_relations(text: str) -> frozenset[tuple[str, str]]:
    return frozenset(
        (match.group("main").casefold(), match.group("condition").casefold())
        for pattern in (_TEMPORAL_POSTFIX, _TEMPORAL_PREFIX)
        for match in pattern.finditer(text)
    )


def _functional_audit_envelope(payload: _WireReview) -> PatchReviewEnvelope:
    contexts = {context.context_id: context for context in payload.document_contexts}
    reviews: list[TextPatchReview] = []
    for audit in payload.sentence_audits:
        context = contexts[audit.context_id]
        required = _feature_relations(audit.source_sentence)
        original = _feature_relations(context.source_block)
        proposed = _feature_relations(context.proposed_block)
        equivalent = required <= proposed <= original and _temporal_relations(
            context.source_block
        ) == _temporal_relations(context.proposed_block)
        reviews.append(
            TextPatchReview(
                block_id=audit.block_id,
                expected_sha256=audit.expected_sha256,
                replacement_sha256=audit.replacement_sha256,
                equivalent=equivalent,
                reason="Each source subject retains its own function and object."
                if equivalent
                else "A source subject loses its function, object, or governing condition.",
            )
        )
    return PatchReviewEnvelope(reviews=reviews)


def _review_response(envelope: PatchReviewEnvelope) -> httpx.Response:
    return httpx.Response(
        HTTPStatus.OK,
        json={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": envelope.model_dump_json()}],
                    },
                    "finishReason": "STOP",
                }
            ]
        },
    )


def _functional_generator(client: genai.Client) -> VertexGeminiPatchGenerator:
    return VertexGeminiPatchGenerator(
        policy=GeminiModelPolicy(
            project="offline-fixture",
            location="global",
            unslop_model="gemini-3.5-flash-lite",
            fallback_model="gemini-3.1-flash-lite",
            restyle_model="gemini-3.8-flash",
        ),
        client=client,
    )


@pytest.mark.parametrize(
    "source",
    [
        "Atlas condenses prose; Prism refines layouts for readers.",
        "Atlas condenses prose, whereas Prism refines layouts for readers.",
        "The suite includes Atlas to condense prose and Prism to refine layouts for readers.",
    ],
)
@pytest.mark.parametrize(
    ("proposed", "approved"),
    [
        ("Prism refines layouts and Atlas condenses prose.", True),
        ("Atlas condenses prose while Prism can work together.", False),
        ("Atlas refines layouts and Prism condenses prose.", False),
        ("Atlas condenses prose and Prism improves readability.", False),
    ],
)
async def test_sdk_reviews_each_feature_function_in_full_reordered_context(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    source: str,
    proposed: str,
    *,
    approved: bool,
) -> None:
    document = parse_markdown(source, title="Feature purposes")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=proposed)
    received: list[_WireReview] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        payload = _WireReview.model_validate_json(contents[0].parts[0].text)
        received.append(payload)
        return _review_response(_functional_audit_envelope(payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        try:
            decision = await _functional_generator(client).review(document, [patch])
        finally:
            await client.aio.aclose()
    assert decision.approved == (frozenset({block.id}) if approved else frozenset())
    assert set(decision.rejected) == (set() if approved else {block.id})
    assert len(received) == 1
    payload = received[0]
    assert len(payload.sentence_audits) == 3
    assert [audit.source_kind for audit in payload.sentence_audits] == [
        "sentence",
        "clause",
        "clause",
    ]
    assert payload.sentence_audits[0].source_sentence == source
    assert all(
        audit.replacement_sha256 == hashlib.sha256(proposed.encode()).hexdigest()
        for audit in payload.sentence_audits
    )
    assert payload.document_contexts[0].source_block == source
    assert payload.document_contexts[0].proposed_block == proposed


@pytest.mark.parametrize(
    ("source", "proposed", "approved"),
    [
        (
            "Cleaner is intended to tighten prose, while Formatter improves presentation.",
            "While Formatter improves presentation, Cleaner is intended to tighten prose.",
            True,
        ),
        (
            "Cleaner is intended to tighten prose, while Formatter improves presentation.",
            "Cleaner is intended to tighten prose while Formatter can work together.",
            False,
        ),
        (
            "Cleaner is intended to tighten prose, while Formatter improves presentation.",
            "Formatter is intended to tighten prose while Cleaner improves presentation.",
            False,
        ),
        (
            "Cleaner is intended to tighten prose, while Formatter improves presentation.",
            "Cleaner tightens prose while Formatter improves presentation.",
            False,
        ),
        (
            "Recorder collects data from sensors while Pump runs.",
            "While Pump runs, Recorder collects data from sensors.",
            True,
        ),
        (
            "Recorder collects data from sensors while Pump runs.",
            "Recorder collects data from sensors.",
            False,
        ),
        (
            "Recorder collects data from sensors while Pump runs.",
            "Recorder collects data from sensors; Pump runs.",
            False,
        ),
        (
            "Recorder collects data from sensors while Pump may not run.",
            "Recorder collects data from sensors while Pump runs.",
            False,
        ),
    ],
)
async def test_sdk_while_focus_reviews_its_function_or_condition_in_parent_context(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    source: str,
    proposed: str,
    *,
    approved: bool,
) -> None:
    document = parse_markdown(source, title="Contextual dependent audit")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=proposed)
    received: list[_WireReview] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        payload = _WireReview.model_validate_json(contents[0].parts[0].text)
        received.append(payload)
        return _review_response(_functional_audit_envelope(payload))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        try:
            decision = await _functional_generator(client).review(document, [patch])
        finally:
            await client.aio.aclose()
    assert decision.approved == (frozenset({block.id}) if approved else frozenset())
    assert set(decision.rejected) == (set() if approved else {block.id})
    assert len(received) == 1
    original, focus = received[0].sentence_audits
    assert original.source_kind == "sentence"
    assert original.source_sentence == source
    assert focus.source_kind == "dependent_clause"
    assert focus.source_sentence.startswith("while ")
    assert focus.parent_sentence == source
    assert focus.source_span is not None
    start, end = focus.source_span
    assert source[start:end] == focus.source_sentence
    assert received[0].document_contexts[0].source_block == source
    assert received[0].document_contexts[0].proposed_block == proposed


def _corrupt_clause_verdict(
    payload: _WireReview, envelope: PatchReviewEnvelope, fault: str
) -> PatchReviewEnvelope:
    index = next(
        index
        for index, audit in enumerate(payload.sentence_audits)
        if audit.source_kind != "sentence"
    )
    reviews = list(envelope.reviews)
    if fault == "missing":
        reviews.pop(index)
    elif fault == "duplicate":
        reviews.append(reviews[index])
    elif fault in {"parent_binding", "span_binding"}:
        reviews[index] = reviews[index].model_copy(
            update={
                "expected_sha256": _altered_focus_hash(
                    payload, payload.sentence_audits[index], fault
                )
            }
        )
    else:
        updates = {
            "unknown_id": {"block_id": reviews[index].block_id + "-unknown"},
            "source_hash": {"expected_sha256": "0" * 64},
            "replacement_hash": {"replacement_sha256": "0" * 64},
        }
        reviews[index] = reviews[index].model_copy(update=updates[fault])
    return PatchReviewEnvelope(reviews=reviews)


def _altered_focus_hash(payload: _WireReview, audit: _WirePair, fault: str) -> str:
    assert audit.source_span is not None
    assert audit.parent_sentence is not None
    context = next(
        item for item in payload.document_contexts if item.context_id == audit.context_id
    )
    start, end = audit.source_span
    parent = audit.parent_sentence
    if fault == "span_binding":
        start += 1
    if fault == "parent_binding":
        parent += " A different condition applies."
    identity = [
        hashlib.sha256(context.source_block.encode()).hexdigest(),
        0,
        audit.source_kind,
        parent,
        start,
        end,
        audit.source_sentence,
    ]
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "unknown_id",
        "source_hash",
        "replacement_hash",
        "parent_binding",
        "span_binding",
    ],
)
@pytest.mark.parametrize(
    "source",
    [
        "Atlas condenses prose; Prism refines layouts for readers.",
        "Recorder collects data from sensors while Pump runs.",
    ],
)
async def test_sdk_clause_verdict_coverage_and_proposal_bindings_fail_closed(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client], fault: str, source: str
) -> None:
    document = parse_markdown(source, title="Bound feature purposes")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    received: list[_WireReview] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        payload = _WireReview.model_validate_json(contents[0].parts[0].text)
        received.append(payload)
        envelope = _corrupt_clause_verdict(payload, _functional_audit_envelope(payload), fault)
        return _review_response(envelope)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        try:
            with pytest.raises(ModelUnavailableError):
                await _functional_generator(client).review(document, [patch])
        finally:
            await client.aio.aclose()
    assert len(received) == 3
    assert all(len(payload.sentence_audits) >= 2 for payload in received)


def _fact_audit_envelope(
    payload: _WireReview, facts: dict[str, tuple[str, ...]], case: str
) -> PatchReviewEnvelope:
    contexts = {context.context_id: context for context in payload.document_contexts}
    reviews: list[TextPatchReview] = []
    for audit in payload.sentence_audits:
        required = facts[audit.source_sentence]
        if case == "omit_editorial" and not required:
            continue
        proposed = contexts[audit.context_id].proposed_block
        equivalent = (
            all(fact in proposed for fact in required) and "previews work offline" not in proposed
        )
        reviews.append(
            TextPatchReview(
                block_id=audit.block_id,
                expected_sha256=audit.expected_sha256,
                replacement_sha256="0" * 64
                if case == "forged_output" and audit == payload.sentence_audits[0]
                else audit.replacement_sha256,
                equivalent=equivalent,
                reason=(
                    "The source facts survive in the complete block; "
                    "fact-free framing may be omitted."
                )
                if equivalent
                else (
                    "A capability, restriction or folder condition is lost, "
                    "or an unsupported claim is added."
                ),
            )
        )
    return PatchReviewEnvelope(reviews=reviews)


def _assert_source_audits(
    payload: _WireReview, source: str, proposed: str, sentences: set[str]
) -> None:
    assert {audit.source_sentence for audit in payload.sentence_audits} == sentences
    assert len(payload.sentence_audits) == len(sentences)
    assert len(payload.document_contexts) == 1
    context = payload.document_contexts[0]
    assert context.source_block == source
    assert context.proposed_block == proposed
    assert all(audit.context_id == context.context_id for audit in payload.sentence_audits)
    assert len({audit.expected_sha256 for audit in payload.sentence_audits}) == len(sentences)
    assert all(
        audit.replacement_sha256 == hashlib.sha256(proposed.encode()).hexdigest()
        for audit in payload.sentence_audits
    )


@pytest.mark.parametrize(
    "case",
    [
        "complete",
        "missing_list_member",
        "changed_force",
        "lost_scope",
        "unsupported",
        "omit_editorial",
        "forged_output",
    ],
)
async def test_sdk_audits_each_source_sentence_against_the_complete_reordered_block(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client], case: str
) -> None:
    facts: dict[str, tuple[str, ...]] = {
        "readers can preview text documents, spreadsheets, presentations and HTML.": (
            "text documents",
            "spreadsheets",
            "presentations",
            "HTML",
        ),
        "they cannot edit the source.": ("cannot edit the source",),
        "previews stay within the selected folder.": ("previews stay within the selected folder",),
        "let us take a thoughtful look.": (),
    }
    source = " ".join(facts)
    compact = (
        "previews stay within the selected folder; readers can preview text documents, "
        "spreadsheets, presentations and HTML but cannot edit the source."
    )
    replacement = {
        "missing_list_member": compact.replace(", spreadsheets", ""),
        "changed_force": compact.replace("cannot", "can"),
        "lost_scope": compact.replace("within", "outside"),
        "unsupported": compact + " previews work offline.",
    }.get(case, compact)
    document = parse_markdown(source, title="Viewer")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)
    received: list[_WireReview] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        payload = _WireReview.model_validate_json(contents[0].parts[0].text)
        received.append(payload)
        envelope = _fact_audit_envelope(payload, facts, case)
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": envelope.model_dump_json()}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.5-flash-lite",
                fallback_model="gemini-3.1-flash-lite",
                restyle_model="gemini-3.8-flash",
            ),
            client=client,
        )
        try:
            if case in {"omit_editorial", "forged_output"}:
                with pytest.raises(ModelUnavailableError):
                    await generator.review(document, [patch])
            else:
                decision = await generator.review(document, [patch])
                assert decision.approved == (
                    frozenset({block.id}) if case == "complete" else frozenset()
                )
                assert set(decision.rejected) == (set() if case == "complete" else {block.id})
        finally:
            await client.aio.aclose()
    assert len(received) == (3 if case in {"omit_editorial", "forged_output"} else 1)
    for payload in received:
        _assert_source_audits(payload, source, replacement, set(facts))
    safety = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    expected = {"changed_force": "force_language_changed", "lost_scope": "scope_qualifier_changed"}
    if case in expected:
        assert not safety.accepted
        assert {finding.code for finding in safety.findings} == {expected[case]}
    else:
        assert safety.accepted == {block.id: replacement}
        assert not safety.findings


@pytest.mark.parametrize("fail", [False, True])
async def test_review_batches_limit_concurrency_and_wait_for_cancellation(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client], *, fail: bool
) -> None:
    source = "The hidden bird can sing unseen from dense scrub beside the path."
    document = parse_markdown("\n\n".join(source for _ in range(25)), title="Concurrent review")
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
        for block in document.blocks
        if block.text.strip()
    ]
    first_id = patches[0].block_id
    started = asyncio.Event()
    active = 0
    peak = 0
    canceled = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak, canceled
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        pairs = _WireReview.model_validate_json(contents[0].parts[0].text).sentence_audits
        active += 1
        peak = max(peak, active)
        if active == 2:
            started.set()
        try:
            await started.wait()
            if fail:
                if pairs[0].block_id == first_id:
                    return httpx.Response(
                        429, json={"error": {"message": "Offline transient fixture"}}
                    )
                await asyncio.Event().wait()
            envelope = PatchReviewEnvelope(
                reviews=[
                    TextPatchReview(
                        block_id=pair.block_id,
                        expected_sha256=pair.expected_sha256,
                        replacement_sha256=pair.replacement_sha256,
                        equivalent=True,
                        reason="The bird's hidden singing ability and location are unchanged.",
                    )
                    for pair in pairs
                ]
            )
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [{"text": envelope.model_dump_json()}],
                            },
                            "finishReason": "STOP",
                        }
                    ]
                },
            )
        except asyncio.CancelledError:
            canceled += 1
            raise
        finally:
            active -= 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.5-flash-lite",
                fallback_model="gemini-3.1-flash-lite",
                restyle_model="gemini-3.8-flash",
            ),
            client=client,
        )
        try:
            if fail:
                with pytest.raises(ModelUnavailableError):
                    await generator.verify(document, patches)
            else:
                assert await generator.verify(document, patches) == {
                    patch.block_id for patch in patches
                }
        finally:
            await client.aio.aclose()
    assert peak == 2
    assert active == 0
    assert canceled >= 1 if fail else canceled == 0


async def test_concurrent_review_calls_share_capacity_and_keep_their_own_results(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    documents = [
        parse_markdown(
            "\n\n".join(
                f"Bird case {index} can sing unseen from dense scrub beside the path."
                for _ in range(index + 1)
            ),
            title=f"Bird {index}",
        )
        for index in range(3)
    ]
    patches = [
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)]
        for document in documents
        for block in document.blocks[-1:]
    ]
    indices = {document.blocks[-1].sha256: index for index, document in enumerate(documents)}
    started = [asyncio.Event() for _ in documents]
    release = [asyncio.Event() for _ in documents]
    completed: list[int] = []
    active = 0
    peak = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        pairs = _WireReview.model_validate_json(contents[0].parts[0].text).sentence_audits
        pair = pairs[0]
        index = indices[pair.expected_sha256]
        active += 1
        peak = max(peak, active)
        started[index].set()
        try:
            await release[index].wait()
            envelope = PatchReviewEnvelope(
                reviews=[
                    TextPatchReview(
                        block_id=pair.block_id,
                        expected_sha256=pair.expected_sha256,
                        replacement_sha256=pair.replacement_sha256,
                        equivalent=True,
                        reason="This bird's hidden singing ability and location are unchanged.",
                    )
                ]
            )
            completed.append(index)
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [{"text": envelope.model_dump_json()}],
                            },
                            "finishReason": "STOP",
                        }
                    ]
                },
            )
        finally:
            active -= 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.5-flash-lite",
                fallback_model="gemini-3.1-flash-lite",
                restyle_model="gemini-3.8-flash",
            ),
            client=client,
        )
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(generator.review(document, proposals))
                    for document, proposals in zip(documents, patches, strict=True)
                ]
                try:
                    async with asyncio.timeout(5):
                        await started[0].wait()
                        await started[1].wait()
                        release[1].set()
                        await started[2].wait()
                        release[2].set()
                        await tasks[2]
                        release[0].set()
                finally:
                    for gate in release:
                        gate.set()
            decisions = [task.result() for task in tasks]
        finally:
            await client.aio.aclose()

    assert peak == 2
    assert active == 0
    assert completed == [1, 2, 0]
    assert [decision.approved for decision in decisions] == [
        frozenset({document.blocks[-1].id}) for document in documents
    ]
    assert all(not decision.rejected for decision in decisions)


@pytest.fixture
def sdk_client_factory() -> Callable[[httpx.AsyncClient], genai.Client]:
    def create(http: httpx.AsyncClient) -> genai.Client:
        return genai.Client(
            vertexai=True,
            project="offline-fixture",
            location="global",
            credentials=Credentials(token="offline-fixture-not-a-real-token"),
            http_options=types.HttpOptions(
                base_url="http://127.0.0.1:9",
                httpx_async_client=http,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    return create


async def test_real_sdk_review_gateway_timeout_uses_the_configured_fallback(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    document = parse_markdown("The hidden bird can sing unseen beside the path.", title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)
    models: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        model = request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent")
        models.append(model)
        if model == "gemini-3.8-flash":
            return httpx.Response(
                HTTPStatus.GATEWAY_TIMEOUT,
                json={"error": {"code": HTTPStatus.GATEWAY_TIMEOUT, "message": "Offline deadline"}},
            )
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        pairs = _WireReview.model_validate_json(contents[0].parts[0].text).sentence_audits
        envelope = PatchReviewEnvelope(
            reviews=[
                TextPatchReview(
                    block_id=pair.block_id,
                    expected_sha256=pair.expected_sha256,
                    replacement_sha256=pair.replacement_sha256,
                    equivalent=True,
                    reason="The bird's hidden singing ability and location are unchanged.",
                )
                for pair in pairs
            ]
        )
        return httpx.Response(
            HTTPStatus.OK,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": envelope.model_dump_json()}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.5-flash-lite",
                fallback_model="gemini-3.1-flash-lite",
                restyle_model="gemini-3.8-flash",
            ),
            client=client,
        )
        try:
            decision = await generator.review(document, [patch])
        finally:
            await client.aio.aclose()
    assert decision.approved == {block.id}
    assert not decision.rejected
    assert models == ["gemini-3.8-flash", "gemini-3.1-flash-lite"]


async def test_real_sdk_serializes_structured_rewrite_and_review_without_sampling(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    document = parse_markdown(
        "The hidden bird can sing from dense scrub beside the path while remaining unseen by observers.",
        title="Offline SDK fixture",
    )
    block = document.blocks[0]
    replacement = "The hidden bird can sing unseen from scrub beside the path."
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)
    responses = iter(
        (
            PatchEnvelope(patches=[patch]).model_dump_json(),
            PatchReviewEnvelope(
                reviews=[
                    TextPatchReview(
                        block_id=block.id,
                        expected_sha256=block.sha256,
                        replacement_sha256=hashlib.sha256(replacement.encode()).hexdigest(),
                        equivalent=True,
                        reason="The bird's hidden singing location and ability are preserved.",
                    )
                ]
            ).model_dump_json(),
        )
    )
    requests: list[_VendorRequest] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(_VendorRequest.model_validate_json(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": next(responses)}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.5-flash-lite",
                fallback_model="gemini-3.1-flash-lite",
                restyle_model="gemini-3.8-flash",
            ),
            client=client,
        )
        try:
            proposals = await generator.generate(document, phase=TransformPhase.UNSLOP)
            assert proposals == [patch]
            assert await generator.verify(document, proposals) == {block.id}
        finally:
            await client.aio.aclose()

    assert len(requests) == 2
    for request in requests:
        assert "temperature" not in request.generation_config.model_fields_set
        assert request.generation_config.response_mime_type == "application/json"
        assert request.generation_config.response_schema is None
    for request, envelope, field in zip(
        requests, (PatchEnvelope, PatchReviewEnvelope), ("patches", "reviews"), strict=True
    ):
        schema = request.generation_config.response_json_schema
        expected = envelope.model_json_schema()
        expected["properties"][field].pop("maxItems")
        assert schema == expected
        assert '"additional_properties"' not in json.dumps(
            request.generation_config.response_json_schema
        )
    assert requests[0].generation_config.thinking_config is not None
    assert (
        requests[0].generation_config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    )
    assert requests[1].generation_config.thinking_config is not None
    assert requests[1].generation_config.thinking_config.thinking_level == types.ThinkingLevel.HIGH


@pytest.mark.parametrize(
    ("review", "oversized", "blank_reason"),
    [(False, False, False), (False, True, False), (True, False, False), (True, False, True)],
)
async def test_json_schema_responses_still_enforce_strict_local_validation(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    *,
    review: bool,
    oversized: bool,
    blank_reason: bool,
) -> None:
    document = parse_markdown(
        "The hidden bird can sing from dense scrub beside the path while remaining unseen by observers.",
        title="Offline SDK fixture",
    )
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement="The hidden bird can sing unseen from scrub beside the path.",
    )
    payload = (
        json.dumps(
            {
                "reviews": [
                    {
                        "block_id": block.id,
                        "expected_sha256": block.sha256,
                        "replacement_sha256": hashlib.sha256(
                            patch.replacement.encode()
                        ).hexdigest(),
                        "equivalent": True if blank_reason else "true",
                        "reason": " "
                        if blank_reason
                        else "All source facts are preserved in this fixture.",
                    }
                ]
            }
        )
        if review
        else json.dumps({"patches": [patch.model_dump()] * 101})
        if oversized
        else '{"patches":[],"unexpected":"must be rejected"}'
    )

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": payload}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.5-flash-lite",
                fallback_model="gemini-3.1-flash-lite",
                restyle_model="gemini-3.8-flash",
            ),
            client=client,
        )
        try:
            operation = (
                generator.verify(document, [patch])
                if review
                else generator.generate(document, phase=TransformPhase.UNSLOP)
            )
            with pytest.raises(ModelUnavailableError):
                await operation
        finally:
            await client.aio.aclose()
