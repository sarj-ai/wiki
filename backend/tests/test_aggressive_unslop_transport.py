from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import json

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
from pydantic import BaseModel, ConfigDict, Field
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


class _VendorRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    generation_config: types.GenerateContentConfig = Field(alias="generationConfig")


class _WirePair(BaseModel):
    model_config = ConfigDict(extra="ignore")
    block_id: str
    expected_sha256: str
    replacement_sha256: str


class _WireReview(BaseModel):
    document_proposals: list[_WirePair]


class _WireText(BaseModel):
    text: str


class _WireContent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    parts: list[_WireText]


class _WireReviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    contents: list[_WireContent]


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
        pairs = _WireReview.model_validate_json(contents[0].parts[0].text).document_proposals
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
        pairs = _WireReview.model_validate_json(contents[0].parts[0].text).document_proposals
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
            http_options=types.HttpOptions(base_url="http://127.0.0.1:9", httpx_async_client=http),
        )

    return create


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
