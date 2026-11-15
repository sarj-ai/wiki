import asyncio
from datetime import timedelta
import json
from typing import Never, override
import uuid

from google import genai
from google.genai import types
from google.oauth2.credentials import Credentials
import httpx
from pydantic import ValidationError
import pytest

from app.document_transform import task_executor as task_executor_module
from app.document_transform.errors import OutputConflictError, SourceChangedError
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.model_planning import (
    CheckpointingPatchGenerator,
    ModelReviewPendingError,
)
from app.document_transform.models import (
    GeminiModelPolicy,
    PatchGenerator,
    RewriteReview,
    VertexGeminiPatchGenerator,
)
from app.document_transform.schemas import (
    DocumentTransformRequest,
    ModelPlanningCheckpoint,
    PatchEnvelope,
    PatchReviewEnvelope,
    TextPatch,
    TextPatchReview,
    TransformPhase,
)
from app.identifiers import TaskId
from app.performance import provider_retry_budget
from app.tasks import service as task_service_module
from app.tasks.errors import TaskExecutionDeferredError
from tests.test_aggressive_unslop_models import _ReviewInput, _supplied_writer_blocks
from tests.test_document_transform import _Gateway
from tests.test_document_transform_task_executor import _claim, _RetryTasks


SOURCE = (
    "Nora will not charge for the three seats. "
    + "The prose drifts in a gentle breeze, with a warm glow and a soft voice. " * 8
)
DRAFT = "Nora will not charge for the three seats."


def _request() -> DocumentTransformRequest:
    return DocumentTransformRequest.model_validate(
        {
            "mode": "unslop",
            "source": {
                "kind": "markdown",
                "content": SOURCE,
                "name": "Private staged fixture",
                "destination_folder_id": "folder",
            },
        }
    )


class _Writer(PatchGenerator):
    supports_semantic_rewrite = True

    def __init__(self) -> None:
        self.writes = 0
        self.reviews = 0
        self.documents: list[str] = []

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("gemini-3.1-pro-preview",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.writes += 1
        self.documents.append(document.text_sha256)
        block = document.blocks[0]
        return [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=DRAFT)]

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        self.reviews += 1
        assert all(patch.expected_sha256 == document.blocks[0].sha256 for patch in patches)
        return RewriteReview(frozenset(patch.block_id for patch in patches), {})


class _EmptyWriter(_Writer):
    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.writes += 1
        return []


async def _saved_plan() -> ModelPlanningCheckpoint:
    saved: list[ModelPlanningCheckpoint] = []

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    generator = CheckpointingPatchGenerator(
        generator=_Writer(), request=_request(), checkpoint=None, save=save
    )
    with pytest.raises(ModelReviewPendingError):
        await generator.generate(
            parse_markdown(SOURCE, title="Private staged fixture"), phase=TransformPhase.UNSLOP
        )
    assert len(saved) == 1
    return saved[0]


async def test_restart_reuses_durable_proposals_and_requires_review_before_google_mutation() -> (
    None
):
    underlying = _Writer()
    saved: list[ModelPlanningCheckpoint] = []

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    task_id = uuid.UUID(int=17)
    gateway = _Gateway()
    generator = CheckpointingPatchGenerator(
        generator=underlying, request=_request(), checkpoint=None, save=save
    )
    executor = DocumentTransformExecutor(gateway=gateway, generator=generator)
    with provider_retry_budget(timedelta(seconds=240)), pytest.raises(ModelReviewPendingError):
        await executor.execute(operation_id=task_id, request=_request())
    assert gateway.draft is None
    assert gateway.applies == 0
    assert underlying.reviews == 0
    assert underlying.writes == 1
    checkpoint = task_service_module._deserialize_checkpoint(
        task_service_module._serialize_checkpoint(saved[0])
    )
    assert isinstance(checkpoint, ModelPlanningCheckpoint)
    resumed = CheckpointingPatchGenerator(
        generator=underlying, request=_request(), checkpoint=checkpoint, save=save
    )
    with provider_retry_budget(timedelta(seconds=240)):
        result = await DocumentTransformExecutor(gateway=gateway, generator=resumed).execute(
            operation_id=task_id, request=_request()
        )
    assert result.validation.protected_content_preserved
    assert result.validation.structure_preserved
    assert underlying.documents.count(checkpoint.document_sha256) == 1
    assert underlying.reviews >= 1
    assert gateway.applies == 1


async def test_empty_initial_proposal_resumes_both_mode_without_a_second_model_call() -> None:
    request = DocumentTransformRequest.model_validate({**_request().model_dump(), "mode": "both"})
    saved: list[ModelPlanningCheckpoint] = []

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    writer, gateway = _EmptyWriter(), _Gateway()
    staged = CheckpointingPatchGenerator(
        generator=writer, request=request, checkpoint=None, save=save
    )
    operation_id = uuid.UUID(int=27)
    with pytest.raises(ModelReviewPendingError):
        await DocumentTransformExecutor(gateway=gateway, generator=staged).execute(
            operation_id=operation_id, request=request
        )
    assert gateway.draft is None
    assert len(saved) == 1
    assert not saved[0].proposals
    resumed = CheckpointingPatchGenerator(
        generator=writer, request=request, checkpoint=saved[0], save=save
    )
    result = await DocumentTransformExecutor(gateway=gateway, generator=resumed).execute(
        operation_id=operation_id, request=request
    )
    assert result.validation.structure_preserved
    assert result.validation.protected_content_preserved
    assert writer.writes == 1
    assert writer.reviews == 0


async def test_ambiguous_checkpoint_commit_recovers_without_regenerating_proposals() -> None:
    saved: list[ModelPlanningCheckpoint] = []
    writer = _Writer()

    async def committed_then_lost(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)
        raise TimeoutError

    generator = CheckpointingPatchGenerator(
        generator=writer, request=_request(), checkpoint=None, save=committed_then_lost
    )
    document = parse_markdown(SOURCE, title="Private staged fixture")
    with pytest.raises(TimeoutError):
        await generator.generate(document, phase=TransformPhase.UNSLOP)
    with pytest.raises(OutputConflictError):
        await generator.generate(document, phase=TransformPhase.UNSLOP)
    with pytest.raises(OutputConflictError):
        await generator.review(document, list(saved[0].proposals))
    resumed = CheckpointingPatchGenerator(
        generator=writer, request=_request(), checkpoint=saved[0], save=committed_then_lost
    )
    assert await resumed.generate(document, phase=TransformPhase.UNSLOP) == list(saved[0].proposals)
    assert writer.writes == 1


async def test_inflight_plan_save_rejects_reentry_and_cancellation_requires_durable_resume() -> (
    None
):
    entered = asyncio.Event()
    writer = _Writer()

    async def save(_: ModelPlanningCheckpoint) -> None:
        entered.set()
        await asyncio.Event().wait()

    generator = CheckpointingPatchGenerator(
        generator=writer, request=_request(), checkpoint=None, save=save
    )
    document = parse_markdown(SOURCE, title="Private staged fixture")
    running = asyncio.create_task(generator.generate(document, phase=TransformPhase.UNSLOP))
    await asyncio.wait_for(entered.wait(), timeout=2)
    try:
        with pytest.raises(OutputConflictError):
            await generator.generate(document, phase=TransformPhase.UNSLOP)
        with pytest.raises(OutputConflictError):
            await generator.review(document, [])
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
    with pytest.raises(OutputConflictError):
        await generator.generate(document, phase=TransformPhase.UNSLOP)
    assert writer.writes == 1
    assert writer.reviews == 0


async def test_review_requires_a_saved_plan_before_calling_the_provider() -> None:
    writer = _Writer()

    async def save(_: ModelPlanningCheckpoint) -> None:
        pytest.fail("Review cannot create the initial plan")

    generator = CheckpointingPatchGenerator(
        generator=writer, request=_request(), checkpoint=None, save=save
    )
    with pytest.raises(OutputConflictError):
        await generator.review(parse_markdown(SOURCE, title="Private staged fixture"), [])
    assert writer.reviews == 0


async def test_review_binds_original_source_after_deterministic_planning_cleanup() -> None:
    original = parse_markdown(SOURCE, title="Private staged fixture")
    cleaned = original.replace_blocks({original.blocks[0].id: DRAFT})
    saved: list[ModelPlanningCheckpoint] = []

    async def save(plan: ModelPlanningCheckpoint) -> None:
        saved.append(plan)

    writer = _Writer()
    staged = CheckpointingPatchGenerator(
        generator=writer, request=_request(), checkpoint=None, save=save
    )
    with pytest.raises(ModelReviewPendingError):
        await staged.generate(cleaned, phase=TransformPhase.UNSLOP)
    assert saved[0].document_sha256 == cleaned.text_sha256 != original.text_sha256
    resumed = CheckpointingPatchGenerator(
        generator=writer, request=_request(), checkpoint=saved[0], save=save
    )
    await resumed.generate(cleaned, phase=TransformPhase.UNSLOP)
    proposals = [
        TextPatch(
            block_id=original.blocks[0].id,
            expected_sha256=original.blocks[0].sha256,
            replacement=DRAFT,
        )
    ]
    decision = await resumed.review(original, proposals)
    assert decision.approved == {original.blocks[0].id}
    assert writer.writes == 1


async def test_changed_source_rejects_saved_proposals_before_review_or_mutation() -> None:
    checkpoint = await _saved_plan()
    writer = _Writer()

    async def forbidden_save(_: ModelPlanningCheckpoint) -> None:
        pytest.fail("Changed source cannot replace a saved model plan")

    generator = CheckpointingPatchGenerator(
        generator=writer, request=_request(), checkpoint=checkpoint, save=forbidden_save
    )
    with pytest.raises(SourceChangedError):
        await generator.generate(
            parse_markdown(SOURCE.replace("not charge", "charge"), title="Private staged fixture"),
            phase=TransformPhase.UNSLOP,
        )
    assert writer.writes == 0
    assert writer.reviews == 0


@pytest.mark.parametrize("change", ["request", "model", "catalog", "pipeline"])
async def test_changed_request_or_policy_rejects_saved_model_plan(change: str) -> None:
    checkpoint = await _saved_plan()
    payload = checkpoint.model_dump()
    fields = {
        "request": "request_sha256",
        "model": "model_ids",
        "catalog": "catalog_sha256",
        "pipeline": "pipeline_version",
    }
    replacements = {
        "request": "0" * 64,
        "model": ("gemini-4-argon",),
        "catalog": "0" * 64,
        "pipeline": "old-pipeline",
    }
    payload[fields[change]] = replacements[change]
    changed = ModelPlanningCheckpoint.model_validate(payload)

    async def forbidden_save(_: ModelPlanningCheckpoint) -> None:
        pytest.fail("Incompatible plan cannot be rewritten")

    with pytest.raises(OutputConflictError):
        CheckpointingPatchGenerator(
            generator=_Writer(), request=_request(), checkpoint=changed, save=forbidden_save
        )


async def test_proposals_are_encrypted_and_unrecognized_checkpoint_fields_are_rejected() -> None:
    checkpoint = await _saved_plan()
    envelope = task_service_module._serialize_checkpoint(checkpoint)
    assert DRAFT not in json.dumps(envelope)
    assert SOURCE not in json.dumps(envelope)
    assert task_service_module._deserialize_checkpoint(envelope) == checkpoint
    with pytest.raises(ValidationError):
        ModelPlanningCheckpoint.model_validate(
            {**checkpoint.model_dump(), "unverified_success": True}
        )


async def test_valid_model_only_checkpoint_does_not_claim_an_external_mutation() -> None:
    checkpoint = await _saved_plan()
    envelope = task_service_module._serialize_checkpoint(checkpoint)
    assert not task_service_module._checkpoint_requires_review(
        task_service_module._TaskJsonObject.model_validate(envelope)
    )
    corrupted = {**envelope, "checkpoint_ciphertext": "invalid-encrypted-plan"}
    assert task_service_module._checkpoint_requires_review(
        task_service_module._TaskJsonObject.model_validate(corrupted)
    )


async def test_staged_writer_and_reviewer_receive_separate_bounded_sdk_deadlines() -> None:
    requests: list[httpx.Request] = []
    saved: list[ModelPlanningCheckpoint] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        prompt = payload["contents"][0]["parts"][0]["text"]
        if prompt.startswith("{"):
            review = _ReviewInput.model_validate_json(prompt)
            envelope = PatchReviewEnvelope(
                reviews=[
                    TextPatchReview(
                        block_id=pair.block_id,
                        expected_sha256=pair.expected_sha256,
                        replacement_sha256=pair.replacement_sha256,
                        equivalent=True,
                        reason="The original facts and restrictions remain unchanged.",
                    )
                    for pair in review.sentence_audits
                ]
            )
        else:
            blocks = _supplied_writer_blocks(request)
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

    async def save(checkpoint: ModelPlanningCheckpoint) -> None:
        saved.append(checkpoint)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        client = genai.Client(
            vertexai=True,
            project="offline-stage-budgets",
            location="global",
            credentials=Credentials(token="offline-test-credential"),
            http_options=types.HttpOptions(base_url="http://127.0.0.1:9", httpx_async_client=http),
        )
        real = VertexGeminiPatchGenerator(
            policy=GeminiModelPolicy(
                project="offline-stage-budgets",
                location="global",
                unslop_model="gemini-3.1-pro-preview",
                fallback_model="gemini-3.1-pro-preview",
                restyle_model="gemini-3.1-pro-preview",
                timeout=timedelta(seconds=180),
                review_timeout=timedelta(seconds=120),
                writer_review_reserve=timedelta(seconds=60),
            ),
            client=client,
        )
        document = parse_markdown(SOURCE, title="Private staged fixture")
        first = CheckpointingPatchGenerator(
            generator=real, request=_request(), checkpoint=None, save=save
        )
        try:
            with (
                provider_retry_budget(timedelta(seconds=240), clock=lambda: 0),
                pytest.raises(ModelReviewPendingError),
            ):
                await first.generate(document, phase=TransformPhase.UNSLOP)
            assert len(requests) == 1
            resumed = CheckpointingPatchGenerator(
                generator=real, request=_request(), checkpoint=saved[0], save=save
            )
            with provider_retry_budget(timedelta(seconds=240), clock=lambda: 0):
                proposals = await resumed.generate(document, phase=TransformPhase.UNSLOP)
                await resumed.review(document, proposals)
            assert len(requests) == 2
            assert requests[0].extensions["timeout"]["read"] == 180
            assert requests[1].extensions["timeout"]["read"] == 120
        finally:
            await client.aio.aclose()


class _SavedProposals:
    async def execute(self, *, operation_id: uuid.UUID, request: DocumentTransformRequest) -> Never:
        raise ModelReviewPendingError


@pytest.mark.parametrize("attempt", [1, 3])
async def test_phase_continuation_keeps_the_existing_task_attempt_bound(attempt: int) -> None:
    tasks = _RetryTasks(attempt_count=attempt)
    task_id = TaskId(uuid.UUID(int=19))
    claim = _claim(task_id, attempt_count=attempt)
    if attempt < 3:
        with pytest.raises(TaskExecutionDeferredError) as deferred:
            await task_executor_module._execute_claimed(
                task_id, tasks=tasks, transform=_SavedProposals(), claim=claim, payload=_request()
            )
        assert tasks.deferred == 1
        assert tasks.failed == 0
        assert deferred.value.http_status == 409
    else:
        result = await task_executor_module._execute_claimed(
            task_id, tasks=tasks, transform=_SavedProposals(), claim=claim, payload=_request()
        )
        assert result.status == "failed"
        assert tasks.last_error_code == "model_review_budget_exhausted"
        assert tasks.failed == 1
        assert tasks.deferred == 0
