import asyncio
from datetime import timedelta

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.ir import parse_markdown
from app.document_transform.model_planning import (
    CheckpointingPatchGenerator,
    ModelReviewPendingError,
)
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.schemas import (
    DocumentTransformRequest,
    ModelPlanningCheckpoint,
    PatchEnvelope,
    TextPatch,
    TransformPhase,
)
from app.performance import provider_retry_budget
from tests.test_aggressive_unslop_models import _supplied_writer_blocks


@pytest.mark.parametrize("staged", [False, True])
async def test_shared_capacity_wait_retains_writer_budget_with_durable_review_handoff(
    *, staged: bool
) -> None:
    held_started = asyncio.Event()
    first_other_started = asyncio.Event()
    release = asyncio.Event()
    now = 0.0
    active = peak = 0
    other_timeouts: list[float] = []
    saved: list[ModelPlanningCheckpoint] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        blocks = _supplied_writer_blocks(request)
        held = blocks[0].text.startswith("Held")
        active += 1
        peak = max(peak, active)
        try:
            if held:
                held_started.set()
            else:
                other_timeouts.append(request.extensions["timeout"]["read"])
                first_other_started.set()
            await release.wait()
            if not held and request.extensions["timeout"]["read"] < 100:
                return httpx.Response(
                    504,
                    json={
                        "error": {
                            "code": 504,
                            "status": "DEADLINE_EXCEEDED",
                            "message": "Controlled provider requires100seconds",
                        }
                    },
                )
            envelope = PatchEnvelope(
                patches=[
                    TextPatch(
                        block_id=block.block_id,
                        expected_sha256=block.sha256,
                        replacement=block.text,
                    )
                    for block in blocks
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
        finally:
            active -= 1

    async def no_delay(_: float) -> None:
        await asyncio.sleep(0)

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = genai.Client(
            vertexai=True,
            project="offline-contended-budgets",
            location="global",
            credentials=Credentials(token="offline-test-credential"),
            http_options=types.HttpOptions(base_url="http://127.0.0.1:9", httpx_async_client=http),
        )
        real = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-contended-budgets",
                location="global",
                unslop_model="gemini-3.1-pro-preview",
                fallback_model="gemini-3.1-pro-preview",
                restyle_model="gemini-3.1-pro-preview",
                timeout=timedelta(seconds=180),
                writer_review_reserve=timedelta(seconds=60),
            ),
            client=client,
            writer_sleep=no_delay,
        )
        source = "\n\n".join(f"Concurrent item {index} stays unchanged." for index in range(41))
        writer = (
            CheckpointingPatchGenerator(
                generator=real,
                request=DocumentTransformRequest.model_validate(
                    {
                        "mode": "unslop",
                        "source": {
                            "kind": "markdown",
                            "content": source,
                            "name": "Concurrent",
                            "destination_folder_id": "folder",
                        },
                    }
                ),
                checkpoint=None,
                save=save,
            )
            if staged
            else real
        )
        with provider_retry_budget(timedelta(seconds=240), clock=lambda: now):
            held = asyncio.create_task(
                real.generate(
                    parse_markdown("Held document stays unchanged.", title="Held"),
                    phase=TransformPhase.UNSLOP,
                )
            )
            other = None
            try:
                await asyncio.wait_for(held_started.wait(), timeout=2)
                other = asyncio.create_task(
                    writer.generate(
                        parse_markdown(source, title="Concurrent"), phase=TransformPhase.UNSLOP
                    )
                )
                await asyncio.wait_for(first_other_started.wait(), timeout=2)
                assert active == 2
                now = 60.0
            finally:
                release.set()
                outcomes = await asyncio.gather(
                    held, *([other] if other else []), return_exceptions=True
                )
                await client.aio.aclose()
        assert peak == 2
        if staged:
            assert isinstance(outcomes[1], ModelReviewPendingError)
            assert other_timeouts == [180, 150]
            assert len(saved) == 1
            assert len(saved[0].proposals) == 41
        else:
            assert isinstance(outcomes[1], ModelUnavailableError)
            assert other_timeouts[0] == 150
            assert other_timeouts[1:]
            assert all(value == 90 for value in other_timeouts[1:])
            assert not saved
