from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import json
from typing import Literal

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
from jsonschema import Draft202012Validator
from pydantic import JsonValue
import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.schemas import PatchReviewEnvelope, TextPatch, TextPatchReview
from tests.test_aggressive_unslop_models import _ReviewInput


@dataclass(frozen=True)
class _ReviewExchange:
    schema: dict[str, JsonValue]
    response: PatchReviewEnvelope


@pytest.fixture
async def review_exchange() -> _ReviewExchange:
    captured: list[_ReviewExchange] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = _ReviewInput.model_validate_json(payload["contents"][0]["parts"][0]["text"])
        response = PatchReviewEnvelope(
            reviews=[
                TextPatchReview(
                    block_id=pair.block_id,
                    expected_sha256=pair.expected_sha256,
                    replacement_sha256=pair.replacement_sha256,
                    equivalent=True,
                    reason="All source facts and restrictions remain unchanged.",
                )
                for pair in inputs.sentence_audits
            ]
        )
        captured.append(
            _ReviewExchange(payload["generationConfig"]["responseJsonSchema"], response)
        )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": response.model_dump_json()}],
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = genai.Client(
            vertexai=True,
            project="offline-bound-review",
            location="global",
            credentials=Credentials(token="offline-test-credential"),
            http_options=types.HttpOptions(base_url="http://127.0.0.1:9", httpx_async_client=http),
        )
        generator = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-bound-review",
                location="global",
                unslop_model="gemini-3.1-pro-preview",
                fallback_model="gemini-3.1-pro-preview",
                restyle_model="gemini-3.1-pro-preview",
                timeout=timedelta(seconds=120),
            ),
            client=client,
        )
        document = parse_markdown(
            "Nora will not charge for the three seats.\n\nJon will not charge for the four seats.",
            title="Private binding fixture",
        )
        proposals = [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=block.text.replace("the ", ""),
            )
            for block in document.blocks
        ]
        try:
            decision = await generator.review(document, proposals)
            assert len(decision.approved) == 2
        finally:
            await client.aio.aclose()
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize(
    "corruption",
    [
        "unknown_id",
        "source_hash",
        "replacement_hash",
        "cross_pair_id",
        "cross_pair_source",
        "cross_pair_replacement",
    ],
)
def test_actual_sdk_request_schema_rejects_unbound_and_cross_proposal_verdicts(
    review_exchange: _ReviewExchange,
    corruption: Literal[
        "unknown_id",
        "source_hash",
        "replacement_hash",
        "cross_pair_id",
        "cross_pair_source",
        "cross_pair_replacement",
    ],
) -> None:
    validator = Draft202012Validator(review_exchange.schema)
    correct = review_exchange.response.model_dump(mode="json")
    assert validator.is_valid(correct)
    corrupted = deepcopy(correct)
    first, second = corrupted["reviews"]
    match corruption:
        case "unknown_id":
            first["block_id"] = "unknown-review"
        case "source_hash":
            first["expected_sha256"] = "f" * 64
        case "replacement_hash":
            first["replacement_sha256"] = "f" * 64
        case "cross_pair_id":
            first["block_id"] = second["block_id"]
        case "cross_pair_source":
            first["expected_sha256"] = second["expected_sha256"]
        case "cross_pair_replacement":
            first["replacement_sha256"] = second["replacement_sha256"]
    assert not validator.is_valid(corrupted)
    correct["reviews"][0]["equivalent"] = False
    assert validator.is_valid(correct)
