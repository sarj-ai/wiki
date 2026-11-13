import asyncio
from collections import Counter
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import uuid

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
import pytest

from app.document_transform.errors import (
    ModelDeadlineError,
    ModelUnavailableError,
    OutputConflictError,
)
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.model_diagnostics import ModelBindingError, ModelCoverageError
from app.document_transform.model_planning import (
    CheckpointingPatchGenerator,
    ModelReviewPendingError,
)
from app.document_transform.models import GeminiModelPolicy, VertexGeminiPatchGenerator
from app.document_transform.schemas import (
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    ModelPlanningCheckpoint,
    TextPatch,
    TransformPhase,
)
from app.identifiers import FileId, TaskId
from app.performance import provider_retry_budget
from app.tasks import service as task_service_module
from tests.test_aggressive_unslop_models import _ReviewInput, _supplied_writer_blocks
from tests.test_document_transform import _Gateway


SOURCE = "\n\n".join(f"Nora will not charge for the {index + 1} seats." for index in range(40))


def request() -> DocumentTransformRequest:
    return DocumentTransformRequest.model_validate(
        {
            "mode": "unslop",
            "source": {
                "kind": "markdown",
                "content": SOURCE,
                "name": "Review recovery fixture",
                "destination_folder_id": "folder",
            },
        }
    )


def response(payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": json.dumps(payload)}]},
                    "finishReason": "STOP",
                }
            ]
        },
    )


class Provider:
    def __init__(self) -> None:
        self.writer_calls = 0
        self.review_calls: list[tuple[str, ...]] = []
        self.failure = True
        self.completed = asyncio.Event()
        self.clock = 0.0
        self.maximum_pairs: int | None = None
        self.sleeps: list[float] = []
        self.retry_after: str | None = None
        self.failure_status = 504
        self.shorten = False

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    async def respond(self, req: httpx.Request) -> httpx.Response:
        prompt = json.loads(req.content)["contents"][0]["parts"][0]["text"]
        if not prompt.startswith("{"):
            self.writer_calls += 1
            return response(
                {
                    "patches": [
                        {
                            "block_id": b.block_id,
                            "expected_sha256": b.sha256,
                            "replacement": b.text.split(". ")[0] + "." if self.shorten else b.text,
                        }
                        for b in _supplied_writer_blocks(req)
                    ]
                }
            )
        pairs = _ReviewInput.model_validate_json(prompt).sentence_audits
        ids = tuple(pair.block_id for pair in pairs)
        self.review_calls.append(ids)
        if self.maximum_pairs is not None and len(pairs) > self.maximum_pairs:
            return httpx.Response(
                504,
                json={
                    "error": {
                        "code": 504,
                        "status": "DEADLINE_EXCEEDED",
                        "message": "controlled batch capacity",
                    }
                },
            )
        if self.failure and not ids[0].startswith("b0"):
            await self.completed.wait()
            await asyncio.sleep(0)
            self.clock = 209.0
            return httpx.Response(
                self.failure_status,
                headers={} if self.retry_after is None else {"Retry-After": self.retry_after},
                json={
                    "error": {
                        "code": self.failure_status,
                        "status": "DEADLINE_EXCEEDED",
                        "message": "controlled provider deadline",
                    }
                },
            )
        self.completed.set()
        return response(
            {
                "reviews": [
                    {
                        "block_id": p.block_id,
                        "expected_sha256": p.expected_sha256,
                        "replacement_sha256": p.replacement_sha256,
                        "equivalent": p.block_id != "b0",
                        "reason": "The quantity and negation must remain unchanged in this sentence.",
                    }
                    for p in pairs
                ]
            }
        )


@asynccontextmanager
async def generator(provider: Provider) -> AsyncGenerator[VertexGeminiPatchGenerator]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider.respond)) as http:
        client = genai.Client(
            vertexai=True,
            project="offline-review-checkpoint",
            location="global",
            credentials=Credentials(token="offline-test-credential"),
            http_options=types.HttpOptions(base_url="http://127.0.0.1:9", httpx_async_client=http),
        )
        try:
            yield VertexGeminiPatchGenerator(
                policy=GeminiModelPolicy(
                    project="offline-review-checkpoint",
                    location="global",
                    unslop_model="gemini-3.1-pro-preview",
                    fallback_model="gemini-3.1-pro-preview",
                    restyle_model="gemini-3.1-pro-preview",
                    timeout=timedelta(seconds=180),
                    review_timeout=timedelta(seconds=120),
                ),
                client=client,
                writer_sleep=provider.sleep,
            )
        finally:
            await client.aio.aclose()


async def saved_writer(provider: Provider, saved: list[ModelPlanningCheckpoint]) -> DocumentIR:
    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    document = parse_markdown(SOURCE, title="Review recovery fixture")
    async with generator(provider) as real:
        staged = CheckpointingPatchGenerator(
            generator=real, request=request(), checkpoint=None, save=save
        )
        with provider_retry_budget(timedelta(seconds=240)), pytest.raises(ModelReviewPendingError):
            await staged.generate(document, phase=TransformPhase.UNSLOP)
    return document


async def test_partial_review_survives_a_new_sdk_client_without_repeating_completed_batches() -> (
    None
):
    provider, saved = Provider(), []
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    async with generator(provider) as real:
        staged = CheckpointingPatchGenerator(
            generator=real, request=request(), checkpoint=saved[-1], save=save
        )
        with (
            provider_retry_budget(timedelta(seconds=240), clock=lambda: provider.clock),
            pytest.raises(ModelReviewPendingError),
        ):
            await staged.review(document, patches)
    completed = [ids for ids in provider.review_calls if ids[0].startswith("b0")]
    assert len(completed) == 1
    provider.failure, provider.clock = False, 0.0
    before = len(provider.review_calls)
    async with generator(provider) as real:
        resumed = CheckpointingPatchGenerator(
            generator=real, request=request(), checkpoint=saved[-1], save=save
        )
        with provider_retry_budget(timedelta(seconds=240)):
            decision = await resumed.review(document, patches)
    assert completed[0] not in provider.review_calls[before:]
    assert provider.writer_calls == 1
    assert "b0" in decision.rejected
    assert decision.approved == {patch.block_id for patch in patches} - {"b0"}


async def test_review_deadline_keeps_completed_rejections_and_provider_attempt_counts_encrypted() -> (
    None
):
    provider, saved = Provider(), []
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    async with generator(provider) as real:
        staged = CheckpointingPatchGenerator(
            generator=real, request=request(), checkpoint=saved[-1], save=save
        )
        with (
            provider_retry_budget(timedelta(seconds=240), clock=lambda: provider.clock),
            pytest.raises((ModelReviewPendingError, ModelDeadlineError)),
        ):
            await staged.review(document, patches)
    payload = saved[-1].model_dump(mode="json")
    assert payload.get("review_batches"), (
        "Validated reviews and started provider attempts must survive the delivery"
    )
    envelope = task_service_module._serialize_checkpoint(saved[-1])
    assert SOURCE not in json.dumps(envelope)
    assert "negation must remain" not in json.dumps(envelope)
    assert task_service_module._deserialize_checkpoint(envelope) == saved[-1]


async def test_capacity_bounded_provider_reviews_every_sentence_and_preserves_a_negative_verdict() -> (
    None
):
    provider, saved = Provider(), []
    provider.failure = False
    provider.maximum_pairs = 16
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)
    async with generator(provider) as real:
        with provider_retry_budget(timedelta(seconds=240)):
            decision = await real.review(document, patches)
    assert decision.approved == {patch.block_id for patch in patches} - {"b0"}
    assert "b0" in decision.rejected


async def review_saved(
    provider: Provider,
    saved: list[ModelPlanningCheckpoint],
    document: DocumentIR,
    patches: list[TextPatch],
) -> None:
    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    async with generator(provider) as real:
        staged = CheckpointingPatchGenerator(
            generator=real, request=request(), checkpoint=saved[-1], save=save
        )
        with provider_retry_budget(timedelta(seconds=240), clock=lambda: provider.clock):
            await staged.review(document, patches)


async def test_review_attempt_bound_survives_three_new_clients_and_never_resets_on_redelivery() -> (
    None
):
    provider, saved = Provider(), []
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)
    for _ in range(2):
        provider.clock = 0.0
        with pytest.raises(ModelReviewPendingError):
            await review_saved(provider, saved, document, patches)
    provider.clock = 0.0
    with pytest.raises(ModelUnavailableError) as caught:
        await review_saved(provider, saved, document, patches)
    assert not caught.value.retryable
    assert max(Counter(provider.review_calls).values()) == 3
    assert all(batch.attempt_count <= 3 for batch in saved[-1].review_batches)
    calls = len(provider.review_calls)
    provider.clock = 0.0
    with pytest.raises(ModelUnavailableError):
        await review_saved(provider, saved, document, patches)
    assert len(provider.review_calls) == calls
    assert provider.writer_calls == 1


async def test_retry_after_is_preserved_across_review_continuation() -> None:
    provider, saved = Provider(), []
    provider.failure_status, provider.retry_after = 429, "8"
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)
    with pytest.raises(ModelReviewPendingError):
        await review_saved(provider, saved, document, patches)
    assert any(batch.retry_not_before is not None for batch in saved[-1].review_batches)
    provider.failure, provider.clock = False, 0.0
    await review_saved(provider, saved, document, patches)
    assert max(provider.sleeps) >= 7


async def test_permanent_provider_denial_is_not_turned_into_a_progress_continuation() -> None:
    provider, saved = Provider(), []
    provider.failure_status = 403
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)
    with pytest.raises(ModelUnavailableError) as caught:
        await review_saved(provider, saved, document, patches)
    assert caught.value.code == "model_permission_denied"
    assert not caught.value.retryable
    assert max(Counter(provider.review_calls).values()) == 1


@pytest.mark.parametrize(
    "change", ["unknown_id", "duplicate_id", "source_hash", "replacement_hash", "missing_ids"]
)
async def test_recovered_verdicts_still_require_exact_identity_and_complete_coverage(
    change: str,
) -> None:
    provider, saved = Provider(), []
    provider.failure = False
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)
    await review_saved(provider, saved, document, patches)
    checkpoint = saved[-1]
    batch = checkpoint.review_batches[0]
    reviews = list(batch.reviews)
    match change:
        case "missing_ids":
            reviews.pop()
        case "duplicate_id":
            reviews.append(reviews[0])
        case _:
            field, value = {
                "unknown_id": ("block_id", "other-source"),
                "source_hash": ("expected_sha256", "0" * 64),
                "replacement_hash": ("replacement_sha256", "0" * 64),
            }[change]
            reviews[0] = reviews[0].model_copy(update={field: value})
    tampered = batch.model_copy(update={"reviews": tuple(reviews)})
    saved.append(
        checkpoint.model_copy(update={"review_batches": (tampered, *checkpoint.review_batches[1:])})
    )
    calls = len(provider.review_calls)
    with pytest.raises(ExceptionGroup) as caught:
        await review_saved(provider, saved, document, patches)
    assert any(
        isinstance(error, ModelBindingError | ModelCoverageError)
        for error in caught.value.exceptions
    )
    assert len(provider.review_calls) == calls


async def test_changed_proposal_must_receive_a_new_review_instead_of_reusing_a_cached_approval() -> (
    None
):
    provider, saved = Provider(), []
    provider.failure = False
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)
    await review_saved(provider, saved, document, patches)
    calls = len(provider.review_calls)
    changed = [
        patch.model_copy(update={"replacement": patch.replacement + " A new claim."})
        if patch.block_id == "b2"
        else patch
        for patch in patches
    ]
    await review_saved(provider, saved, document, changed)
    assert len(provider.review_calls) == calls + 1
    assert "b2" in provider.review_calls[-1]


async def test_optional_review_deadline_stays_a_model_failure_after_initial_review_completed() -> (
    None
):
    provider, saved = Provider(), []
    provider.failure = False
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    async with generator(provider) as real:
        staged = CheckpointingPatchGenerator(
            generator=real, request=request(), checkpoint=saved[-1], save=save
        )
        with provider_retry_budget(timedelta(seconds=240)):
            await staged.review(document, patches)
        count = len(saved)
        provider.failure, provider.clock = True, 0.0
        with (
            provider_retry_budget(timedelta(seconds=240), clock=lambda: provider.clock),
            pytest.raises(ModelDeadlineError),
        ):
            await staged.review(document, patches)
        assert len(saved) == count


async def test_ambiguous_review_checkpoint_commit_recovers_validated_results_without_repeating_them() -> (
    None
):
    provider, saved = Provider(), []
    provider.failure = False
    document = await saved_writer(provider, saved)
    patches = list(saved[-1].proposals)
    lost = False

    async def committed_then_lost(plan: ModelPlanningCheckpoint) -> None:
        nonlocal lost
        saved.append(plan)
        if not lost and any(batch.reviews for batch in plan.review_batches):
            lost = True
            raise TimeoutError

    async with generator(provider) as real:
        staged = CheckpointingPatchGenerator(
            generator=real, request=request(), checkpoint=saved[-1], save=committed_then_lost
        )
        with provider_retry_budget(timedelta(seconds=240)), pytest.raises(ExceptionGroup):
            await staged.review(document, patches)
    completed = {
        tuple(review.block_id for review in batch.reviews)
        for batch in saved[-1].review_batches
        if batch.reviews
    }
    assert completed
    count = len(provider.review_calls)
    await review_saved(provider, saved, document, patches)
    assert completed.isdisjoint(provider.review_calls[count:])


class GenerationStore:
    def __init__(self) -> None:
        self.saved: list[DocumentTransformCheckpoint] = []
        self.resources: list[FileId] = []

    async def save_document_transform_checkpoint(
        self,
        task_id: TaskId,
        *,
        lease_token: uuid.UUID,
        expected_generation: int,
        checkpoint: DocumentTransformCheckpoint,
    ) -> int:
        assert task_id == uuid.UUID(int=601)
        assert lease_token == uuid.UUID(int=602)
        assert expected_generation == len(self.saved)
        self.saved.append(checkpoint)
        await asyncio.sleep(0)
        return len(self.saved)

    async def record_resource(
        self, task_id: TaskId, *, lease_token: uuid.UUID, resource_id: FileId
    ) -> None:
        assert task_id == uuid.UUID(int=601)
        assert lease_token == uuid.UUID(int=602)
        self.resources.append(resource_id)


async def test_concurrent_review_saves_handoff_the_exact_cas_generation_to_google_mutation() -> (
    None
):
    provider = Provider()
    provider.failure = False
    provider.shorten = True
    payload = request().model_dump(mode="json")
    payload["source"]["content"] = "\n\n".join(
        "Nora will not charge for the three seats. The purpose of this explanation is to provide an overview of the arrangement."
        for _ in range(40)
    )
    fixture_request = DocumentTransformRequest.model_validate(payload)
    store, gateway = GenerationStore(), _Gateway()
    async with generator(provider) as real:

        async def save_initial(plan: ModelPlanningCheckpoint) -> None:
            await first.save_model_planning_checkpoint(plan)

        staged = CheckpointingPatchGenerator(
            generator=real, request=fixture_request, checkpoint=None, save=save_initial
        )
        first = DocumentTransformExecutor(
            gateway=gateway,
            generator=staged,
            checkpoint_store=store,
            task_id=TaskId(uuid.UUID(int=601)),
            lease_token=uuid.UUID(int=602),
        )
        with provider_retry_budget(timedelta(seconds=240)), pytest.raises(ModelReviewPendingError):
            await first.execute(operation_id=uuid.UUID(int=601), request=fixture_request)
    assert gateway.draft is None
    assert gateway.applies == 0
    checkpoint = store.saved[-1]
    assert isinstance(checkpoint, ModelPlanningCheckpoint)
    async with generator(provider) as real:

        async def save(plan: ModelPlanningCheckpoint) -> None:
            await executor.save_model_planning_checkpoint(plan)

        staged = CheckpointingPatchGenerator(
            generator=real, request=fixture_request, checkpoint=checkpoint, save=save
        )
        executor = DocumentTransformExecutor(
            gateway=gateway,
            generator=staged,
            checkpoint_store=store,
            task_id=TaskId(uuid.UUID(int=601)),
            lease_token=uuid.UUID(int=602),
            checkpoint_generation=1,
        )
        with provider_retry_budget(timedelta(seconds=240)):
            result = await executor.execute(
                operation_id=uuid.UUID(int=601), request=fixture_request
            )
        assert result.validation.protected_content_preserved
        assert result.validation.structure_preserved
        assert len([cp for cp in store.saved if isinstance(cp, ModelPlanningCheckpoint)]) > 2
        assert store.resources == ["output"]
        assert gateway.applies == 1
        with pytest.raises(OutputConflictError):
            await executor.save_model_planning_checkpoint(checkpoint)
