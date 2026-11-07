from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
from http import HTTPStatus
import json
import re
from typing import TYPE_CHECKING, Literal

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
from googleapiclient.http import HttpMock, HttpRequest
import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock, parse_markdown
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
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
from app.document_transform.unslop import model_editable_blocks
from app.drive.retry import execute_with_retry
from app.identifiers import FileId, ModelId
from app.performance import (
    begin_request_metrics,
    current_request_metrics,
    provider_retry_budget,
    provider_retry_remaining_seconds,
    reset_request_metrics,
)


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable, Coroutine
    from pathlib import Path

    from httplib2 import Response


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
    block_kind: BlockKind


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


class _WireConcisionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_words: int
    max_words: int
    draft_target_words: int


class _WireRewriteBlock(BaseModel):
    model_config = ConfigDict(extra="ignore")
    block_id: str
    sha256: str
    text: str
    concision_target: _WireConcisionTarget | None


class _WireRewriteBlocks(RootModel[list[_WireRewriteBlock]]):
    """Actual submitted source blocks from the offline writer request."""


class _WireRepairProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")
    proposed: str
    rejected_by: list[str]
    review_feedback: list[str]


class _WireRepairProposals(RootModel[list[_WireRepairProposal]]):
    """Actual repair critique accompanying the unchanged source payload."""


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


def _review_response(envelope: PatchReviewEnvelope | PatchEnvelope) -> httpx.Response:
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


async def _yield_writer_sleep(_delay: float) -> None:
    await asyncio.sleep(0)


def _functional_generator(
    client: genai.Client,
    *,
    writer_sleep: Callable[[float], Awaitable[None]] | None = _yield_writer_sleep,
) -> VertexGeminiPatchGenerator:
    return VertexGeminiPatchGenerator(
        policy=GeminiModelPolicy(
            project="offline-fixture",
            location="global",
            unslop_model="gemini-3.5-flash-lite",
            fallback_model="gemini-3.1-flash-lite",
            restyle_model="gemini-3.8-flash",
        ),
        client=client,
        writer_sleep=writer_sleep,
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
    assert received[0].document_contexts[0].block_kind is BlockKind.PARAGRAPH
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
    match fault:
        case "missing":
            reviews.pop(index)
        case "duplicate":
            reviews.append(reviews[index])
        case "parent_binding" | "span_binding":
            reviews[index] = reviews[index].model_copy(
                update={
                    "expected_sha256": _altered_focus_hash(
                        payload, payload.sentence_audits[index], fault
                    )
                }
            )
        case _:
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


@pytest.mark.parametrize(
    "status",
    [HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.GATEWAY_TIMEOUT],
    ids=["rate-limit", "gateway-timeout"],
)
@pytest.mark.parametrize(
    "primary",
    [ModelId("gemini-3.8-flash"), ModelId("gemini-3.1-pro-preview")],
    ids=["default-flash", "explicit-pro"],
)
async def test_review_collision_uses_a_sticky_configured_high_fallback(
    status: HTTPStatus,
    primary: ModelId,
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    document = parse_markdown("The hidden bird can sing unseen beside the path.", title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)
    models: list[ModelId] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(ModelId(request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent")))
        config = _VendorRequest.model_validate_json(request.content).generation_config
        assert config.thinking_config is not None
        assert config.thinking_config.thinking_level == types.ThinkingLevel.HIGH
        if len(models) < 3:
            return httpx.Response(status, json={"error": {"code": int(status)}})
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        wire = _WireReview.model_validate_json(contents[0].parts[0].text)
        assert wire.document_contexts[0].source_block == block.text
        assert wire.document_contexts[0].proposed_block == patch.replacement
        assert [audit.block_id for audit in wire.sentence_audits] == [block.id]
        return _review_response(_functional_audit_envelope(wire))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model=primary,
                fallback_model="gemini-3.1-pro-preview",
                restyle_model="gemini-3.1-pro-preview",
            ),
            client=client,
        )
        try:
            decision = await generator.review(document, [patch])
        finally:
            await client.aio.aclose()

    assert decision.approved == {block.id}
    assert not decision.rejected
    assert models == [
        ModelId("gemini-3.1-pro-preview"),
        primary,
        primary,
    ]


@pytest.mark.parametrize(
    "field",
    ["expected_sha256", "replacement_sha256"],
    ids=["source-binding", "replacement-binding"],
)
async def test_distinct_review_fallback_cannot_approve_a_wrong_binding(
    field: Literal["expected_sha256", "replacement_sha256"],
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    document = parse_markdown("The hidden bird can sing unseen beside the path.", title="Bird")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)
    models: list[ModelId] = []

    def respond(request: httpx.Request) -> httpx.Response:
        models.append(ModelId(request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent")))
        if len(models) == 1:
            return httpx.Response(HTTPStatus.TOO_MANY_REQUESTS, json={"error": {"code": 429}})
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        wire = _WireReview.model_validate_json(contents[0].parts[0].text)
        envelope = _functional_audit_envelope(wire)
        envelope.reviews = [
            review.model_copy(update={field: "0" * 64}) for review in envelope.reviews
        ]
        return _review_response(envelope)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.8-flash",
                fallback_model="gemini-3.1-pro-preview",
                restyle_model="gemini-3.1-pro-preview",
            ),
            client=client,
        )
        try:
            with pytest.raises(ModelUnavailableError):
                await generator.review(document, [patch])
        finally:
            await client.aio.aclose()

    assert models == [
        ModelId("gemini-3.1-pro-preview"),
        ModelId("gemini-3.8-flash"),
        ModelId("gemini-3.8-flash"),
    ]


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


@pytest.mark.parametrize("fault", ["none", "missing", "replacement_hash"])
async def test_sdk_keeps_heading_and_body_contexts_distinct_and_all_verdicts_bound(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client], fault: str
) -> None:
    source = "Atlas condenses prose; Prism refines layouts for readers."
    document = parse_markdown("## " + source + "\n\n" + source, title="Feature roles")
    blocks = [block for block in document.blocks if block.text]
    proposed = "Prism refines layouts and Atlas condenses prose."
    patches = [
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=proposed)
        for block in blocks
    ]
    received: list[_WireReview] = []

    def respond(request: httpx.Request) -> httpx.Response:
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        payload = _WireReview.model_validate_json(contents[0].parts[0].text)
        received.append(payload)
        response = _functional_audit_envelope(payload)
        if fault == "missing":
            response = PatchReviewEnvelope(reviews=response.reviews[1:])
        elif fault == "replacement_hash":
            response = PatchReviewEnvelope(
                reviews=[
                    response.reviews[0].model_copy(update={"replacement_sha256": "0" * 64}),
                    *response.reviews[1:],
                ]
            )
        return _review_response(response)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        try:
            generator = _functional_generator(client)
            if fault == "none":
                assert (await generator.review(document, patches)).approved == {
                    block.id for block in blocks
                }
            else:
                with pytest.raises(ModelUnavailableError):
                    await generator.review(document, patches)
        finally:
            await client.aio.aclose()
    for payload in received:
        _assert_context_and_hash_audits(payload, blocks, proposed)


def _assert_context_and_hash_audits(
    payload: _WireReview, blocks: list[TextBlock], proposed: str
) -> None:
    assert [context.block_kind for context in payload.document_contexts] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
    ]
    contexts = {context.context_id: context for context in payload.document_contexts}
    for block in blocks:
        audits = [
            audit
            for audit in payload.sentence_audits
            if audit.block_id == block.id or audit.block_id.startswith(block.id + "-")
        ]
        assert {audit.source_kind for audit in audits} == {"sentence", "clause"}
        assert all(contexts[audit.context_id].block_kind is block.kind for audit in audits)
        assert all(
            audit.replacement_sha256 == hashlib.sha256(proposed.encode()).hexdigest()
            for audit in audits
        )


@pytest.mark.parametrize(
    ("source", "unsafe", "required", "unsupported"),
    [
        (
            "Wiki supports finding files by name or content, and its search shortcut is Ctrl+K.",
            "With the Ctrl+K search shortcut, users can either navigate structure or search content.",
            ("name", "content", "Ctrl+K"),
            "",
        ),
        (
            "Unslop is intended to tighten the writing and remove filler, while Format improves the document’s presentation.",
            "Wiki includes Unslop to tighten text, while Format and Format settings refine presentation.",
            ("Unslop", "tighten", "filler", "Format", "presentation"),
            "Format settings",
        ),
        (
            "The latency figures are historical reference estimates, rather than promises about current hardware performance.",
            "The latency figures are reference estimates.",
            ("historical", "current hardware performance"),
            "",
        ),
    ],
)
@pytest.mark.parametrize("preserve", [False, True])
async def test_sdk_honors_controlled_fact_verdicts_without_asserting_model_quality(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    source: str,
    unsafe: str,
    required: tuple[str, ...],
    unsupported: str,
    *,
    preserve: bool,
) -> None:
    document = parse_markdown(source, title="Source facts")
    block = document.blocks[0]
    proposed = source if preserve else unsafe
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=proposed)

    def respond(request: httpx.Request) -> httpx.Response:
        contents = _WireReviewRequest.model_validate_json(request.content).contents
        payload = _WireReview.model_validate_json(contents[0].parts[0].text)
        contexts = {context.context_id: context for context in payload.document_contexts}
        reviews = []
        for audit in payload.sentence_audits:
            context = contexts[audit.context_id]
            assert context.source_block == source
            assert context.proposed_block == proposed
            equivalent = all(term in context.proposed_block for term in required) and not (
                unsupported and unsupported in context.proposed_block
            )
            reviews.append(
                TextPatchReview(
                    block_id=audit.block_id,
                    expected_sha256=audit.expected_sha256,
                    replacement_sha256=audit.replacement_sha256,
                    equivalent=equivalent,
                    reason="The explicit source fact set is preserved."
                    if equivalent
                    else "A concrete search mode, feature purpose or historical caveat is missing or invented.",
                )
            )
        return _review_response(PatchReviewEnvelope(reviews=reviews))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        try:
            decision = await _functional_generator(client).review(document, [patch])
        finally:
            await client.aio.aclose()
    assert decision.approved == (frozenset({block.id}) if preserve else frozenset())
    assert set(decision.rejected) == (set() if preserve else {block.id})


@pytest.mark.parametrize("fail", [False, True])
async def test_sdk_writer_batches_share_cap_preserve_order_and_reap_cancellation(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client], *, fail: bool
) -> None:
    source = "The hidden bird can sing unseen from dense scrub beside the path."
    document = parse_markdown("\n\n".join(source for _ in range(13)), title="Writer concurrency")
    blocks = [block for block in document.blocks if block.text]
    first_id = blocks[0].id
    started = asyncio.Event()
    active = 0
    peak = 0
    canceled = 0
    completed: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak, canceled
        assert request.headers.get("X-Server-Timeout") == "90"
        assert (
            _VendorRequest.model_validate_json(request.content).generation_config.max_output_tokens
            == 16_384
        )
        contents = _WireReviewRequest.model_validate_json(request.content).contents[0].parts[0].text
        batch = _WireRewriteBlocks.model_validate_json(
            next(line for line in contents.splitlines() if line.startswith("["))
        ).root
        active += 1
        peak = max(peak, active)
        if active == 2:
            started.set()
        try:
            patches = await _writer_batch_patches(batch, first_id, started, fail=fail)
            completed.append(batch[0].block_id)
            return httpx.Response(
                HTTPStatus.OK,
                json={
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {"text": PatchEnvelope(patches=patches).model_dump_json()}
                                ],
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
        try:
            generator = _functional_generator(client)
            async with asyncio.timeout(5):
                if fail:
                    with pytest.raises(ModelUnavailableError):
                        await generator.generate(document, phase=TransformPhase.UNSLOP)
                else:
                    results = await asyncio.gather(
                        generator.generate(document, phase=TransformPhase.UNSLOP),
                        generator.generate(document, phase=TransformPhase.UNSLOP),
                    )
                    assert all(
                        [
                            (patch.block_id, patch.expected_sha256, patch.replacement)
                            for patch in result
                        ]
                        == [(block.id, block.sha256, block.text) for block in blocks]
                        for result in results
                    )
        finally:
            await client.aio.aclose()
    assert peak == 2
    assert active == 0
    if fail:
        assert canceled >= 1
    else:
        assert not canceled
        assert completed[0] != first_id


async def _writer_batch_patches(
    batch: list[_WireRewriteBlock], first_id: str, started: asyncio.Event, *, fail: bool
) -> list[TextPatch]:
    await started.wait()
    if fail and batch[0].block_id != first_id:
        await asyncio.Event().wait()
    await asyncio.sleep(0.03 if batch[0].block_id == first_id else 0)
    patches = [
        TextPatch(block_id=block.block_id, expected_sha256=block.sha256, replacement=block.text)
        for block in batch
    ]
    if fail:
        patches = []
    return patches


async def test_sdk_concision_repair_keeps_numeric_budget_and_full_source_audits(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    fact = "The sensor may record temperature at night."
    source = (
        fact
        + (
            " The explanation describes the discussion in expanded wording before returning to the description."
        )
        * 4
    )
    document = parse_markdown(source, title="Sensor")
    block = document.blocks[0]
    rewrites: list[_WireRewriteBlock] = []
    reviews: list[_WireReview] = []
    critiques: list[_WireRepairProposal] = []

    def respond(request: httpx.Request) -> httpx.Response:
        prompt = _WireReviewRequest.model_validate_json(request.content).contents[0].parts[0].text
        if prompt.startswith("{"):
            payload = _WireReview.model_validate_json(prompt)
            reviews.append(payload)
            return _review_response(
                PatchReviewEnvelope(
                    reviews=[
                        TextPatchReview(
                            block_id=audit.block_id,
                            expected_sha256=audit.expected_sha256,
                            replacement_sha256=audit.replacement_sha256,
                            equivalent=True,
                            reason="The controlled verdict retains the sensor's qualified measurement claim.",
                        )
                        for audit in payload.sentence_audits
                    ]
                )
            )
        arrays = [line for line in prompt.splitlines() if line.startswith("[")]
        current = _WireRewriteBlocks.model_validate_json(arrays[0]).root[0]
        _assert_concision_wire_source(current, block.sha256, source)
        if rewrites:
            feedback = _WireRepairProposals.model_validate_json(arrays[1]).root[0]
            critiques.append(feedback)
            assert feedback.proposed == source
            assert feedback.rejected_by == ["concision_target_unmet"]
            assert "proposal 59 words; maximum 23" in feedback.review_feedback[0]
        rewrites.append(current)
        patch = TextPatch(
            block_id=block.id,
            expected_sha256=block.sha256,
            replacement=source if len(rewrites) == 1 else fact,
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
        client = sdk_client_factory(http)
        try:
            generator = _functional_generator(client)
            result = await TextTransformPlanner(
                generator=generator, allow_unslop_model=True
            ).transform(document, _document_budget_request(source))
        finally:
            await client.aio.aclose()
    assert len(rewrites) == 2
    assert rewrites[0] == rewrites[1]
    assert [critique.rejected_by for critique in critiques] == [["concision_target_unmet"]]
    assert result.document.blocks[0].text == fact
    assert result.model_count == 1
    assert not result.findings
    assert all(
        context.source_block == source and context.proposed_block == fact
        for payload in reviews
        for context in payload.document_contexts
    )
    assert reviews


def _document_budget_request(source: str) -> DocumentTransformRequest:
    return DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content=source,
            name="Concision",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.UNSLOP,
    )


async def test_sdk_document_budget_is_shared_across_separate_editor_batches(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    fact = (
        "The logger records temperature at night, stores the readings in its archive, "
        "and sends the complete log to the laboratory."
    )
    padding = " The account restates this description in extended and repetitive wording."
    source = "\n\n".join(fact + padding * 8 for _ in range(7))
    document = parse_markdown(source, title="Batch allocation")
    blocks = model_editable_blocks(document)
    replacements = {
        block.id: fact + padding * extra
        for block, extra in zip(blocks, (4, 2, 2, 2, 2, 2, 0), strict=True)
    }
    writer_batches: list[list[_WireRewriteBlock]] = []
    reviews: list[_WireReview] = []

    def respond(request: httpx.Request) -> httpx.Response:
        prompt = _WireReviewRequest.model_validate_json(request.content).contents[0].parts[0].text
        if prompt.startswith("{"):
            payload = _WireReview.model_validate_json(prompt)
            reviews.append(payload)
            assert all(
                context.source_block == fact + padding * 8 for context in payload.document_contexts
            )
            assert all(
                context.proposed_block.startswith(fact) for context in payload.document_contexts
            )
            return _review_response(
                PatchReviewEnvelope(
                    reviews=[
                        TextPatchReview(
                            block_id=audit.block_id,
                            expected_sha256=audit.expected_sha256,
                            replacement_sha256=audit.replacement_sha256,
                            equivalent=True,
                            reason=(
                                "The exact temperature, archive and laboratory facts remain; "
                                "only repeated editorial wording is deleted."
                            ),
                        )
                        for audit in payload.sentence_audits
                    ]
                )
            )
        arrays = [line for line in prompt.splitlines() if line.startswith("[")]
        assert len(arrays) == 1
        batch = _WireRewriteBlocks.model_validate_json(arrays[0]).root
        writer_batches.append(batch)
        return _review_response(
            PatchEnvelope(
                patches=[
                    TextPatch(
                        block_id=block.block_id,
                        expected_sha256=block.sha256,
                        replacement=replacements[block.block_id],
                    )
                    for block in batch
                ]
            )
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        try:
            result = await TextTransformPlanner(
                generator=_functional_generator(client), allow_unslop_model=True
            ).transform(document, _document_budget_request(source))
        finally:
            await client.aio.aclose()

    assert len(blocks) == 7
    assert len(writer_batches) > 1
    assert {block.block_id for batch in writer_batches for block in batch} == replacements.keys()
    assert any(
        sum(len(replacements[block.block_id].split()) for block in batch)
        > sum(len(block.text.split()) for block in batch) * 2 // 5
        for batch in writer_batches
    )
    assert sum(len(block.text.split()) for block in blocks) == 700
    assert sum(len(block.text.split()) for block in result.document.blocks) == 280
    assert result.document == document.replace_blocks(replacements)
    assert result.model_count == 7
    assert not result.findings
    assert reviews
    _assert_document_budget_audits(reviews, blocks, replacements, (fact, *([padding.strip()] * 8)))


def _assert_document_budget_audits(
    reviews: list[_WireReview],
    blocks: tuple[TextBlock, ...],
    replacements: dict[str, str],
    source_sentences: tuple[str, ...],
) -> None:
    audited_blocks: set[str] = set()
    for review in reviews:
        contexts = {context.context_id: context for context in review.document_contexts}
        for audit in review.sentence_audits:
            family = [
                block
                for block in blocks
                if audit.block_id == block.id or audit.block_id.startswith(block.id + "-")
            ]
            assert len(family) == 1
            block = family[0]
            context = contexts[audit.context_id]
            assert context.source_block == block.text
            assert context.proposed_block == replacements[block.id]
            _assert_document_budget_source_hash(block, audit, source_sentences)
            assert (
                audit.replacement_sha256
                == hashlib.sha256(replacements[block.id].encode()).hexdigest()
            )
            audited_blocks.add(block.id)
    assert audited_blocks == replacements.keys()


def _assert_document_budget_source_hash(
    block: TextBlock, audit: _WirePair, sentences: tuple[str, ...]
) -> None:
    prefix = block.id + "-sentence-"
    assert audit.block_id.startswith(prefix)
    index = int(audit.block_id.removeprefix(prefix).split("-", 1)[0])
    assert 0 <= index < len(sentences)
    identity: list[str | int]
    if audit.source_kind == "sentence":
        assert audit.block_id == prefix + str(index)
        assert audit.source_sentence == sentences[index]
        assert audit.parent_sentence is audit.source_span is None
        identity = [block.sha256, index, sentences[index]]
    else:
        assert audit.parent_sentence is not None
        assert audit.parent_sentence == sentences[index]
        assert audit.source_span is not None
        start, end = audit.source_span
        assert 0 <= start < end <= len(block.text)
        assert audit.source_sentence == block.text[start:end]
        assert audit.block_id == f"{prefix}{index}-{audit.source_kind}-{start}-{end}"
        identity = [
            block.sha256,
            index,
            audit.source_kind,
            audit.parent_sentence,
            start,
            end,
            audit.source_sentence,
        ]
    assert (
        audit.expected_sha256
        == hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    )


def _assert_concision_wire_source(
    block: _WireRewriteBlock, expected_sha256: str, source: str
) -> None:
    assert block.sha256 == expected_sha256
    assert block.text == source
    assert block.concision_target is not None
    assert block.concision_target.source_words == 59
    assert block.concision_target.max_words == 24
    assert block.concision_target.draft_target_words == 19


@pytest.mark.parametrize("repair_fault", [False, True], ids=["repair-success", "repair-504"])
@pytest.mark.parametrize("fallback", ["gemini-3.1-pro-preview", "gemini-3.1-flash-lite"])
async def test_sdk_initial_fallback_and_flash_repair_keep_independent_review_high(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    fallback: str,
    *,
    repair_fault: bool,
) -> None:
    source = "The hidden bird can sing unseen from dense scrub beside the path."
    document = parse_markdown(source, title="Thinking roles")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    received: list[tuple[str, types.GenerateContentConfig]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        model = request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent")
        config = _VendorRequest.model_validate_json(request.content).generation_config
        received.append((model, config))
        assert request.headers.get("X-Server-Timeout") == "90"
        assert config.max_output_tokens == 16_384
        if len(received) == 1 or (repair_fault and len(received) == 3):
            return httpx.Response(HTTPStatus.GATEWAY_TIMEOUT, json={"error": {"code": 504}})
        prompt = _WireReviewRequest.model_validate_json(request.content).contents[0].parts[0].text
        if prompt.startswith("{"):
            payload = _WireReview.model_validate_json(prompt)
            return _review_response(
                PatchReviewEnvelope(
                    reviews=[
                        TextPatchReview(
                            block_id=audit.block_id,
                            expected_sha256=audit.expected_sha256,
                            replacement_sha256=audit.replacement_sha256,
                            equivalent=True,
                            reason="The offline source facts are unchanged.",
                        )
                        for audit in payload.sentence_audits
                    ]
                )
            )
        batch = _WireRewriteBlocks.model_validate_json(
            next(line for line in prompt.splitlines() if line.startswith("["))
        ).root
        assert [(item.block_id, item.sha256, item.text) for item in batch] == [
            (block.id, block.sha256, source)
        ]
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
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model="gemini-3.8-flash",
                fallback_model=fallback,
                restyle_model="gemini-3.1-pro-preview",
            ),
            client=client,
        )
        try:
            assert await generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
            assert await generator.repair(
                document,
                [patch],
                (
                    TransformFinding(
                        code="semantic_review_rejected",
                        block_id=block.id,
                        detail="Check each unchanged source fact.",
                    ),
                ),
            ) == [patch]
            decision = await generator.review(document, [patch])
            assert decision.approved == {block.id}
            assert not decision.rejected
        finally:
            await client.aio.aclose()
    assert [model for model, _config in received] == [
        "gemini-3.8-flash",
        fallback,
        "gemini-3.8-flash",
        *([fallback] if repair_fault else []),
        "gemini-3.1-pro-preview",
    ]
    levels = [
        config.thinking_config.thinking_level if config.thinking_config else None
        for _model, config in received
    ]
    assert levels == [
        types.ThinkingLevel.LOW,
        types.ThinkingLevel.MEDIUM if fallback == "gemini-3.1-pro-preview" else None,
        types.ThinkingLevel.HIGH,
        *(
            [types.ThinkingLevel.HIGH if fallback == "gemini-3.1-pro-preview" else None]
            if repair_fault
            else []
        ),
        types.ThinkingLevel.HIGH,
    ]


async def test_sdk_pro_primary_override_and_format_keep_high_thinking(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
) -> None:
    source = "The hidden bird can sing unseen from dense scrub beside the path."
    document = parse_markdown(source, title="Pro compatibility")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    received: list[types.GenerateContentConfig] = []

    def respond(request: httpx.Request) -> httpx.Response:
        received.append(_VendorRequest.model_validate_json(request.content).generation_config)
        envelope = PatchEnvelope(patches=[patch] if len(received) == 1 else [])
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
                unslop_model="gemini-3.1-pro-preview",
                fallback_model="gemini-3.1-pro-preview",
                restyle_model="gemini-3.1-pro-preview",
            ),
            client=client,
        )
        try:
            assert await generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
            assert await generator.generate(document, phase=TransformPhase.FORMAT) == []
        finally:
            await client.aio.aclose()
    assert len(received) == 2
    assert all(config.thinking_config is not None for config in received)
    assert [
        config.thinking_config.thinking_level for config in received if config.thinking_config
    ] == [types.ThinkingLevel.HIGH, types.ThinkingLevel.HIGH]


@dataclass(slots=True)
class _ModelBudgetClock:
    now: float = 0.0

    def read(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass(frozen=True, slots=True)
class _DeadlineDocument:
    document: DocumentIR
    patch: TextPatch


@pytest.fixture
def model_budget_clock() -> _ModelBudgetClock:
    return _ModelBudgetClock()


@pytest.fixture
def deadline_document() -> _DeadlineDocument:
    source = "The hidden bird can sing unseen from dense scrub beside the path."
    document = parse_markdown(source, title="Deadline protocol")
    block = document.blocks[0]
    return _DeadlineDocument(
        document,
        TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source),
    )


@asynccontextmanager
async def _deadline_generator(
    factory: Callable[[httpx.AsyncClient], genai.Client],
    respond: (
        Callable[[httpx.Request], httpx.Response]
        | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]
    ),
    *,
    writer_sleep: Callable[[float], Awaitable[None]] | None = _yield_writer_sleep,
) -> AsyncGenerator[VertexGeminiPatchGenerator]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = factory(http)
        try:
            async with asyncio.timeout(5):
                yield _functional_generator(client, writer_sleep=writer_sleep)
        finally:
            await client.aio.aclose()


def _deadline_response(request: httpx.Request) -> httpx.Response:
    prompt = _WireReviewRequest.model_validate_json(request.content).contents[0].parts[0].text
    if prompt.startswith("{"):
        audits = _WireReview.model_validate_json(prompt).sentence_audits
        return _review_response(
            PatchReviewEnvelope(
                reviews=[
                    TextPatchReview(
                        block_id=audit.block_id,
                        expected_sha256=audit.expected_sha256,
                        replacement_sha256=audit.replacement_sha256,
                        equivalent=True,
                        reason="The exact offline source facts and relationships are unchanged.",
                    )
                    for audit in audits
                ]
            )
        )
    batch = _WireRewriteBlocks.model_validate_json(
        next(line for line in prompt.splitlines() if line.startswith("["))
    ).root
    patches = [
        TextPatch(
            block_id=block.block_id,
            expected_sha256=block.sha256,
            replacement=block.text,
        )
        for block in batch
    ]
    if "Return an empty patches array." in prompt:
        patches = []
    return httpx.Response(
        HTTPStatus.OK,
        json={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": PatchEnvelope(patches=patches).model_dump_json()}],
                    },
                    "finishReason": "STOP",
                }
            ]
        },
    )


async def _generate_with_operation_budget(
    generator: VertexGeminiPatchGenerator,
    document: DocumentIR,
    seconds: float,
    *,
    clock: Callable[[], float],
) -> list[TextPatch]:
    with provider_retry_budget(timedelta(seconds=seconds), clock=clock):
        return await generator.generate(document, phase=TransformPhase.UNSLOP)


async def _call_model_operation(
    generator: VertexGeminiPatchGenerator,
    deadline_document: _DeadlineDocument,
    *,
    review: bool,
) -> None:
    if review:
        await generator.review(deadline_document.document, [deadline_document.patch])
    else:
        await generator.generate(deadline_document.document, phase=TransformPhase.UNSLOP)


@pytest.mark.parametrize("review", [False, True])
async def test_model_deadline_expired_before_call_never_submits_to_sdk(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
    *,
    review: bool,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with (
            provider_retry_budget(timedelta(seconds=30), clock=model_budget_clock.read),
            pytest.raises(ModelUnavailableError),
        ):
            await _call_model_operation(generator, deadline_document, review=review)
    assert not requests
    assert model_budget_clock.now == 0


@pytest.mark.parametrize("external_cancel", [False, True])
async def test_model_deadline_bounds_contended_slots_without_releasing_others(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
    *,
    external_cancel: bool,
) -> None:
    owners_started = asyncio.Event()
    release_owners = asyncio.Event()
    submitted = 0
    active = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal submitted, active
        submitted += 1
        active += 1
        if active == 2:
            owners_started.set()
        try:
            await release_owners.wait()
            return _deadline_response(request)
        finally:
            active -= 1

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        async with asyncio.TaskGroup() as group:
            owners = [
                group.create_task(
                    generator.generate(deadline_document.document, phase=TransformPhase.UNSLOP)
                )
                for _ in range(2)
            ]
            try:
                await owners_started.wait()
                queued = asyncio.create_task(
                    _generate_with_operation_budget(
                        generator, deadline_document.document, 31.05, clock=model_budget_clock.read
                    )
                )
                if external_cancel:
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    queued.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await queued
                else:
                    with pytest.raises(ModelUnavailableError):
                        await queued
                assert submitted == 2
                assert active == 2
            finally:
                release_owners.set()
        assert all(owner.result() == [deadline_document.patch] for owner in owners)
        assert await generator.generate(
            deadline_document.document, phase=TransformPhase.UNSLOP
        ) == [deadline_document.patch]
    assert submitted == 3
    assert active == 0
    assert model_budget_clock.now == 0


async def test_model_timeout_is_recomputed_after_waiting_for_shared_slot(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
) -> None:
    owners_started = asyncio.Event()
    release = asyncio.Event()
    timeouts: list[str | None] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        if len(timeouts) == 2:
            owners_started.set()
        await release.wait()
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        async with asyncio.TaskGroup() as group:
            for _ in range(2):
                group.create_task(
                    generator.generate(deadline_document.document, phase=TransformPhase.UNSLOP)
                )
            try:
                await owners_started.wait()
                queued = group.create_task(
                    _generate_with_operation_budget(
                        generator, deadline_document.document, 70, clock=model_budget_clock.read
                    )
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                model_budget_clock.advance(35)
            finally:
                release.set()
        assert queued.result() == [deadline_document.patch]
    assert timeouts == ["90", "90", "5"]


async def test_review_without_minimum_budget_does_not_submit_to_sdk(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        return httpx.Response(HTTPStatus.GATEWAY_TIMEOUT, json={"error": {"code": 504}})

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with (
            provider_retry_budget(timedelta(seconds=31.5), clock=model_budget_clock.read),
            pytest.raises(ModelUnavailableError),
        ):
            await _call_model_operation(generator, deadline_document, review=True)
    assert not timeouts
    assert model_budget_clock.now == 0


@pytest.mark.parametrize("repair", [False, True])
async def test_unslop_fallback_reserves_time_for_independent_review(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
    *,
    repair: bool,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        if len(timeouts) == 1:
            model_budget_clock.advance(90)
            return httpx.Response(HTTPStatus.GATEWAY_TIMEOUT, json={"error": {"code": 504}})
        if len(timeouts) == 2:
            assert request.extensions["timeout"] == {
                "connect": 60,
                "read": 60,
                "write": 60,
                "pool": 60,
            }
        else:
            config = _VendorRequest.model_validate_json(request.content).generation_config
            assert config.thinking_config is not None
            assert config.thinking_config.thinking_level == types.ThinkingLevel.HIGH
        model_budget_clock.advance(60)
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with provider_retry_budget(timedelta(seconds=240), clock=model_budget_clock.read):
            if repair:
                patches = await generator.repair(
                    deadline_document.document,
                    [deadline_document.patch],
                    (
                        TransformFinding(
                            code="semantic_review_rejected",
                            block_id=deadline_document.patch.block_id,
                            detail="Review the source relationship before accepting this rewrite.",
                        ),
                    ),
                )
            else:
                patches = await generator.generate(
                    deadline_document.document, phase=TransformPhase.UNSLOP
                )
            assert patches == [deadline_document.patch]
            assert provider_retry_remaining_seconds() == 90
            review = await generator.review(deadline_document.document, patches)
            assert review.approved == {deadline_document.patch.block_id}
            assert not review.rejected
            assert provider_retry_remaining_seconds() == 30
    assert timeouts == ["90", "60", "60"]
    assert model_budget_clock.now == 210
    assert provider_retry_remaining_seconds() is None


async def test_unslop_fallback_cannot_spend_the_reserved_review_budget(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        model_budget_clock.advance(90)
        return httpx.Response(HTTPStatus.GATEWAY_TIMEOUT, json={"error": {"code": 504}})

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with provider_retry_budget(timedelta(seconds=180), clock=model_budget_clock.read):
            with pytest.raises(ModelUnavailableError):
                await generator.generate(deadline_document.document, phase=TransformPhase.UNSLOP)
            assert provider_retry_remaining_seconds() == 90
    assert timeouts == ["90"]
    assert model_budget_clock.now == 90
    assert provider_retry_remaining_seconds() is None


async def test_unslop_fallback_without_a_deadline_keeps_native_timeout(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        if len(timeouts) == 1:
            return httpx.Response(HTTPStatus.GATEWAY_TIMEOUT, json={"error": {"code": 504}})
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        assert await generator.generate(
            deadline_document.document, phase=TransformPhase.UNSLOP
        ) == [deadline_document.patch]
    assert timeouts == ["90", "90"]
    assert provider_retry_remaining_seconds() is None


@pytest.mark.parametrize("seconds", [44.5, 45], ids=["below-minimum", "at-minimum"])
async def test_semantic_review_requires_minimum_remaining_model_budget(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
    seconds: float,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with provider_retry_budget(timedelta(seconds=seconds), clock=model_budget_clock.read):
            if seconds < 45:
                with pytest.raises(ModelUnavailableError):
                    await generator.review(deadline_document.document, [deadline_document.patch])
            else:
                review = await generator.review(
                    deadline_document.document, [deadline_document.patch]
                )
                assert review.approved == {deadline_document.patch.block_id}
                assert not review.rejected
    assert timeouts == ([] if seconds < 45 else ["15"])
    assert model_budget_clock.now == 0


async def test_semantic_review_retry_with_less_than_minimum_budget_makes_no_rpc(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        model_budget_clock.advance(16)
        return httpx.Response(HTTPStatus.GATEWAY_TIMEOUT, json={"error": {"code": 504}})

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with provider_retry_budget(timedelta(seconds=60), clock=model_budget_clock.read):
            with pytest.raises(ModelUnavailableError):
                await generator.review(deadline_document.document, [deadline_document.patch])
            assert provider_retry_remaining_seconds() == 44
    assert timeouts == ["30"]
    assert model_budget_clock.now == 16
    assert provider_retry_remaining_seconds() is None


async def test_review_retry_recomputes_timeout_after_admitted_backoff(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        if len(timeouts) == 1:
            model_budget_clock.advance(10)
            return httpx.Response(HTTPStatus.GATEWAY_TIMEOUT, json={"error": {"code": 504}})
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with provider_retry_budget(timedelta(seconds=100), clock=model_budget_clock.read):
            result = await generator.review(deadline_document.document, [deadline_document.patch])
    assert result.approved == {deadline_document.patch.block_id}
    assert not result.rejected
    assert timeouts == ["70", "60"]


@pytest.mark.parametrize("external_cancel", [False, True])
async def test_model_deadline_or_cancellation_reaps_active_and_queued_sdk_batches(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
    *,
    external_cancel: bool,
) -> None:
    document = parse_markdown(
        "\n\n".join(deadline_document.patch.replacement for _ in range(13)),
        title="Deadline cancellation",
    )
    started = asyncio.Event()
    release = asyncio.Event()
    submitted = 0
    active = 0
    canceled = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal submitted, active, canceled
        submitted += 1
        active += 1
        if active == 2:
            started.set()
        try:
            await release.wait()
            return _deadline_response(request)
        except asyncio.CancelledError:
            canceled += 1
            raise
        finally:
            active -= 1

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        operation = asyncio.create_task(
            _generate_with_operation_budget(
                generator, document, 32.5, clock=model_budget_clock.read
            )
        )
        try:
            await started.wait()
            if external_cancel:
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            else:
                model_budget_clock.advance(2)
                with pytest.raises(ModelUnavailableError):
                    await operation
        finally:
            release.set()
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        assert submitted == 2
        assert active == 0
        assert canceled == 2
        assert await generator.generate(
            deadline_document.document, phase=TransformPhase.UNSLOP
        ) == [deadline_document.patch]
    assert submitted == 3
    assert model_budget_clock.now == (0 if external_cancel else 2)


async def test_parallel_operations_keep_separate_deadlines_on_cached_generator(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
) -> None:
    short_started = asyncio.Event()
    long_finished = asyncio.Event()
    other_source = "The quiet bird can rest unseen within dense scrub beside the path."
    other = parse_markdown(other_source, title="Other operation")
    canceled = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal canceled
        prompt = _WireReviewRequest.model_validate_json(request.content).contents[0].parts[0].text
        if other_source in prompt:
            await short_started.wait()
            long_finished.set()
            return _deadline_response(request)
        short_started.set()
        try:
            await asyncio.Event().wait()
            pytest.fail("the short request must be canceled by its own deadline")
        except asyncio.CancelledError:
            canceled += 1
            raise

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        short = asyncio.create_task(
            _generate_with_operation_budget(
                generator, deadline_document.document, 31.05, clock=model_budget_clock.read
            )
        )
        try:
            await short_started.wait()
            result = await _generate_with_operation_budget(
                generator, other, 100, clock=model_budget_clock.read
            )
            assert long_finished.is_set()
            assert [patch.replacement for patch in result] == [other_source]
            with pytest.raises(ModelUnavailableError):
                await short
        finally:
            if not short.done():
                short.cancel()
                await asyncio.gather(short, return_exceptions=True)
    assert canceled == 1
    assert provider_retry_remaining_seconds() is None
    assert model_budget_clock.now == 0


def _drive_response_content(_response: Response, content: bytes) -> bytes:
    return content


async def test_model_completion_preserves_drive_budget_and_restores_parent_context(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
    tmp_path: Path,
) -> None:
    timeouts: list[str | None] = []
    response_path = tmp_path / "drive-response.txt"
    response_path.write_bytes(b"drive-ready")
    request = HttpRequest(
        HttpMock(str(response_path), {"status": "200"}),
        postproc=_drive_response_content,
        uri="https://offline.invalid/drive-response",
    )

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        model_budget_clock.advance(20)
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        with provider_retry_budget(timedelta(seconds=60), clock=model_budget_clock.read):
            assert await generator.generate(
                deadline_document.document, phase=TransformPhase.UNSLOP
            ) == [deadline_document.patch]
            assert provider_retry_remaining_seconds() == 40
            assert execute_with_retry(request) == b"drive-ready"
    assert timeouts == ["30"]
    assert provider_retry_remaining_seconds() is None


async def test_no_operation_deadline_keeps_native_timeout_and_format_protocol(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
) -> None:
    timeouts: list[str | None] = []

    def respond(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.headers.get("X-Server-Timeout"))
        config = _VendorRequest.model_validate_json(request.content).generation_config
        assert config.max_output_tokens == 16_384
        return _deadline_response(request)

    async with _deadline_generator(sdk_client_factory, respond) as generator:
        assert await generator.generate(
            deadline_document.document, phase=TransformPhase.UNSLOP
        ) == [deadline_document.patch]
        assert (
            await generator.generate(deadline_document.document, phase=TransformPhase.FORMAT) == []
        )
        result = await generator.review(deadline_document.document, [deadline_document.patch])
    assert result.approved == {deadline_document.patch.block_id}
    assert not result.rejected
    assert timeouts == ["90", "90", "90"]


async def test_shared_provider_clock_follows_nested_budgets_and_threads(
    model_budget_clock: _ModelBudgetClock,
) -> None:
    other_clock = _ModelBudgetClock(now=1000)
    with provider_retry_budget(timedelta(seconds=100), clock=model_budget_clock.read):
        model_budget_clock.advance(5)
        assert provider_retry_remaining_seconds() == 95
        with provider_retry_budget(timedelta(seconds=20)):
            model_budget_clock.advance(3)
            assert await asyncio.to_thread(provider_retry_remaining_seconds) == 17
        assert provider_retry_remaining_seconds() == 92
        with provider_retry_budget(timedelta(seconds=40), clock=other_clock.read):
            other_clock.advance(7)
            assert await asyncio.to_thread(provider_retry_remaining_seconds) == 33
        assert provider_retry_remaining_seconds() == 92
    assert provider_retry_remaining_seconds() is None


def test_shared_provider_clock_follows_request_metrics_and_reset(
    model_budget_clock: _ModelBudgetClock,
) -> None:
    previous_metrics = current_request_metrics()
    with provider_retry_budget(timedelta(seconds=100), clock=model_budget_clock.read):
        model_budget_clock.advance(5)
        assert provider_retry_remaining_seconds() == 95
        tokens = begin_request_metrics()
        try:
            assert current_request_metrics() is not previous_metrics
            assert provider_retry_remaining_seconds() == 20
            model_budget_clock.advance(3)
            assert provider_retry_remaining_seconds() == 17
        finally:
            reset_request_metrics(tokens)
        assert current_request_metrics() is previous_metrics
        assert provider_retry_remaining_seconds() == 92
    assert provider_retry_remaining_seconds() is None


@dataclass(slots=True)
class _WriterAdmissionWait:
    entered: asyncio.Queue[float]
    release: asyncio.Event
    delays: list[float]

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await self.entered.put(delay)
        await self.release.wait()


@pytest.fixture
def writer_admission_wait() -> _WriterAdmissionWait:
    return _WriterAdmissionWait(asyncio.Queue(), asyncio.Event(), [])


async def test_paced_writer_starts_keep_two_real_sdk_requests_active_and_ordered(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    writer_admission_wait: _WriterAdmissionWait,
) -> None:
    document = parse_markdown(
        "\n\n".join(deadline_document.patch.replacement for _ in range(13)),
        title="Paced writer admission",
    )
    first = asyncio.Event()
    two = asyncio.Event()
    finish = asyncio.Event()
    requests: list[httpx.Request] = []
    active = 0
    peak = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        requests.append(request)
        active += 1
        peak = max(peak, active)
        first.set()
        if active == 2:
            two.set()
        try:
            await finish.wait()
            return _deadline_response(request)
        finally:
            active -= 1

    async with _deadline_generator(
        sdk_client_factory, respond, writer_sleep=writer_admission_wait
    ) as generator:
        async with asyncio.TaskGroup() as group:
            operation = group.create_task(generator.generate(document, phase=TransformPhase.UNSLOP))
            await first.wait()
            assert len(requests) == active == 1
            try:
                delay = await writer_admission_wait.entered.get()
                assert 0 < delay <= 1.25
                writer_admission_wait.release.set()
                await two.wait()
                assert len(requests) == active == peak == 2
            finally:
                writer_admission_wait.release.set()
                finish.set()
        assert [patch.block_id for patch in operation.result()] == [
            block.id for block in model_editable_blocks(document)
        ]
    assert len(requests) == 3
    assert active == 0
    assert peak == 2


def _empty_writer_response() -> httpx.Response:
    return httpx.Response(
        HTTPStatus.OK,
        json={
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": PatchEnvelope(patches=[]).model_dump_json()}],
                    },
                    "finishReason": "STOP",
                }
            ]
        },
    )


@pytest.mark.parametrize(
    "status",
    [HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.GATEWAY_TIMEOUT, HTTPStatus.BAD_REQUEST, None],
)
async def test_writer_backoff_only_follows_transient_errors_without_extra_attempts(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    writer_admission_wait: _WriterAdmissionWait,
    status: HTTPStatus | None,
) -> None:
    requests: list[httpx.Request] = []
    transient = status in {HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.GATEWAY_TIMEOUT}

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            await asyncio.sleep(1.3)
            return (
                _empty_writer_response()
                if status is None
                else httpx.Response(status, json={"error": {"code": status}})
            )
        return _deadline_response(request)

    if not transient:
        writer_admission_wait.release.set()
    async with _deadline_generator(
        sdk_client_factory, respond, writer_sleep=writer_admission_wait
    ) as generator:
        async with asyncio.TaskGroup() as group:
            operation = group.create_task(
                generator.generate(deadline_document.document, phase=TransformPhase.UNSLOP)
            )
            try:
                if transient:
                    delay = await writer_admission_wait.entered.get()
                    assert 1 <= delay <= 1.25
                    assert len(requests) == 1
            finally:
                writer_admission_wait.release.set()
        assert operation.result() == [deadline_document.patch]
    models = [request.url.path.rsplit("/", 1)[-1].split(":", 1)[0] for request in requests]
    assert models == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    assert bool(writer_admission_wait.delays) is transient


async def test_writer_wait_rechecks_shared_budget_before_submitting_another_request(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    model_budget_clock: _ModelBudgetClock,
    writer_admission_wait: _WriterAdmissionWait,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _deadline_response(request)

    async with _deadline_generator(
        sdk_client_factory, respond, writer_sleep=writer_admission_wait
    ) as generator:
        assert await generator.generate(
            deadline_document.document, phase=TransformPhase.UNSLOP
        ) == [deadline_document.patch]
        operation = asyncio.create_task(
            _generate_with_operation_budget(
                generator, deadline_document.document, 35, clock=model_budget_clock.read
            )
        )
        try:
            await writer_admission_wait.entered.get()
            model_budget_clock.advance(6)
            writer_admission_wait.release.set()
            with pytest.raises(ModelUnavailableError):
                await operation
        finally:
            writer_admission_wait.release.set()
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
    assert len(requests) == 1
    assert model_budget_clock.now == 6
    assert provider_retry_remaining_seconds() is None


async def test_cancelled_writer_wait_releases_admission_lock_and_rpc_capacity(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    deadline_document: _DeadlineDocument,
    writer_admission_wait: _WriterAdmissionWait,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _deadline_response(request)

    async with _deadline_generator(
        sdk_client_factory, respond, writer_sleep=writer_admission_wait
    ) as generator:
        assert await generator.generate(
            deadline_document.document, phase=TransformPhase.UNSLOP
        ) == [deadline_document.patch]
        async with asyncio.TaskGroup() as group:
            owner = group.create_task(
                generator.generate(deadline_document.document, phase=TransformPhase.UNSLOP)
            )
            await writer_admission_wait.entered.get()
            queued = group.create_task(
                generator.generate(deadline_document.document, phase=TransformPhase.UNSLOP)
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert len(writer_admission_wait.delays) == 1
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            await writer_admission_wait.entered.get()
            assert len(requests) == 1
            writer_admission_wait.release.set()
        assert queued.result() == [deadline_document.patch]
        assert await generator.generate(
            deadline_document.document, phase=TransformPhase.UNSLOP
        ) == [deadline_document.patch]
    assert len(requests) == 3


@pytest.mark.parametrize(
    ("primary", "initial_level", "repair_level"),
    [
        pytest.param(
            ModelId("gemini-3.8-flash"),
            types.ThinkingLevel.LOW,
            types.ThinkingLevel.HIGH,
            id="default-flash-editor",
        ),
        pytest.param(
            ModelId("gemini-3.1-pro-preview"),
            types.ThinkingLevel.HIGH,
            types.ThinkingLevel.HIGH,
            id="explicit-pro-editor",
        ),
        pytest.param(
            ModelId("gemini-3.5-flash-lite"),
            types.ThinkingLevel.MEDIUM,
            types.ThinkingLevel.HIGH,
            id="legacy-explicit-editor",
        ),
    ],
)
async def test_sdk_repair_uses_the_configured_editor_while_format_and_review_use_restyle(
    sdk_client_factory: Callable[[httpx.AsyncClient], genai.Client],
    primary: ModelId,
    initial_level: types.ThinkingLevel,
    repair_level: types.ThinkingLevel,
) -> None:
    source = "The hidden bird can sing unseen from dense scrub beside the path."
    document = parse_markdown(source, title="Editorial roles")
    block = document.blocks[0]
    patch = TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=source)
    received: list[tuple[str, types.GenerateContentConfig]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Server-Timeout") == "90"
        received.append(
            (
                request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent"),
                _VendorRequest.model_validate_json(request.content).generation_config,
            )
        )
        return _deadline_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = sdk_client_factory(http)
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-fixture",
                location="global",
                unslop_model=primary,
                fallback_model="gemini-3.1-pro-preview",
                restyle_model="gemini-3.1-pro-preview",
            ),
            client=client,
            writer_sleep=_yield_writer_sleep,
        )
        try:
            assert await generator.generate(document, phase=TransformPhase.UNSLOP) == [patch]
            assert await generator.repair(
                document,
                [patch],
                (
                    TransformFinding(
                        code="semantic_review_rejected",
                        block_id=block.id,
                        detail="Retain the exact source action and condition.",
                    ),
                ),
            ) == [patch]
            decision = await generator.review(document, [patch])
            assert decision.approved == {block.id}
            assert not decision.rejected
            assert await generator.generate(document, phase=TransformPhase.FORMAT) == []
        finally:
            await client.aio.aclose()
    assert [model for model, _config in received] == [
        primary,
        primary,
        "gemini-3.1-pro-preview",
        "gemini-3.1-pro-preview",
    ]
    assert [
        config.thinking_config.thinking_level if config.thinking_config else None
        for _model, config in received
    ] == [initial_level, repair_level, types.ThinkingLevel.HIGH, types.ThinkingLevel.HIGH]
    assert all(config.max_output_tokens == 16_384 for _model, config in received)
