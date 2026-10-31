from __future__ import annotations

from collections.abc import Callable
import hashlib

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
from pydantic import BaseModel, ConfigDict, Field
import pytest

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
        assert request.generation_config.response_schema is not None
    assert requests[0].generation_config.thinking_config is not None
    assert requests[0].generation_config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert requests[1].generation_config.thinking_config is not None
    assert (
        requests[1].generation_config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    )
