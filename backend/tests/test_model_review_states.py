from datetime import UTC, datetime, timedelta
import hashlib

from pydantic import JsonValue, RootModel, ValidationError
import pytest

from app.document_transform.errors import OutputConflictError
from app.document_transform.review_checkpoint import (
    ReviewBatchProgress,
    ReviewCheckpointStore,
    checkpointed_review_scope,
)
from app.document_transform.schemas import (
    ModelPlanningCheckpoint,
    ModelReviewAttemptCheckpoint,
    ModelReviewBackoffCheckpoint,
    ModelReviewBatchCheckpoint,
    ModelReviewCompletedCheckpoint,
    PatchReviewEnvelope,
    TextPatchReview,
)
from app.identifiers import ModelReviewInputHash
from tests.test_document_model_checkpoint import _saved_plan


PROMPT = "Review the exact saved source and proposed replacement."
INPUT_HASH = ModelReviewInputHash(hashlib.sha256(PROMPT.encode()).hexdigest())


class _ReviewBatchPayload(RootModel[ModelReviewBatchCheckpoint]):
    pass


def review(*, equivalent: bool = True) -> PatchReviewEnvelope:
    return PatchReviewEnvelope(
        reviews=[
            TextPatchReview(
                block_id="b0",
                expected_sha256="1" * 64,
                replacement_sha256="2" * 64,
                equivalent=equivalent,
                reason="The replacement preserves the source quantity and negation.",
            )
        ]
    )


async def test_review_attempt_backoff_completion_and_duplicate_outcomes() -> None:
    saved: list[ModelReviewBatchCheckpoint] = []

    async def save(batch: ModelReviewBatchCheckpoint) -> None:
        saved.append(batch)

    with checkpointed_review_scope(ReviewCheckpointStore(get=lambda _: None, save=save)):
        progress = ReviewBatchProgress(PROMPT)
    await progress.begin(1)
    assert isinstance(saved[-1], ModelReviewAttemptCheckpoint)
    with pytest.raises(OutputConflictError):
        await progress.begin(1)
    await progress.defer(1, 60)
    backoff = saved[-1]
    assert isinstance(backoff, ModelReviewBackoffCheckpoint)
    assert 0 < progress.retry_delay <= 60
    await progress.defer(1, 120)
    assert saved[-1] is backoff
    assert len(saved) == 2
    with pytest.raises(OutputConflictError):
        await progress.complete(1, review())
    await progress.begin(2)
    with pytest.raises(OutputConflictError):
        await progress.complete(1, review())
    await progress.complete(2, review())
    assert isinstance(saved[-1], ModelReviewCompletedCheckpoint)
    assert progress.reviews == review()
    assert progress.retry_delay == 0
    await progress.complete(2, review())
    assert len(saved) == 4
    with pytest.raises(OutputConflictError):
        await progress.begin(3)
    with pytest.raises(OutputConflictError):
        await progress.defer(2, 0)
    with pytest.raises(OutputConflictError):
        await progress.complete(2, review(equivalent=False))


async def test_review_state_changes_only_after_the_durable_save() -> None:
    async def fail(_: ModelReviewBatchCheckpoint) -> None:
        raise TimeoutError

    with checkpointed_review_scope(ReviewCheckpointStore(get=lambda _: None, save=fail)):
        progress = ReviewBatchProgress(PROMPT)
    with pytest.raises(TimeoutError):
        await progress.begin(1)
    assert progress.attempt_count == 0
    assert progress.reviews is None


@pytest.mark.parametrize("delay", [-1, float("inf"), float("nan")])
async def test_invalid_backoff_is_rejected_without_replacing_the_attempt(delay: float) -> None:
    progress = ReviewBatchProgress(PROMPT)
    await progress.begin(1)
    with pytest.raises(ValueError, match="finite and nonnegative"):
        await progress.defer(1, delay)
    assert progress.attempt_count == 1
    assert progress.retry_delay == 0


def test_persisted_batch_must_belong_to_the_requested_input() -> None:
    foreign = ModelReviewAttemptCheckpoint(
        input_sha256=ModelReviewInputHash("0" * 64), attempt_count=1
    )

    async def save(_: ModelReviewBatchCheckpoint) -> None:
        pytest.fail("A foreign checkpoint cannot be saved")

    with (
        checkpointed_review_scope(ReviewCheckpointStore(get=lambda _: foreign, save=save)),
        pytest.raises(OutputConflictError),
    ):
        ReviewBatchProgress(PROMPT)


@pytest.mark.parametrize("status", ["attempted", "backoff", "completed"])
async def test_legacy_checkpoint_is_decoded_without_replaying_or_resetting_attempts(
    status: str,
) -> None:
    plan = await _saved_plan()
    legacy: dict[str, JsonValue] = {
        "input_sha256": INPUT_HASH,
        "attempt_count": 2,
        "retry_not_before": None,
        "reviews": [],
    }
    if status in {"backoff", "completed"}:
        legacy["retry_not_before"] = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
    if status == "completed":
        legacy["reviews"] = [item.model_dump(mode="json") for item in review().reviews]
    restored = ModelPlanningCheckpoint.model_validate(
        {**plan.model_dump(), "version": 1, "review_batches": (legacy,)}
    )
    assert restored.version == 2
    batch = restored.review_batches[0]
    assert batch.status == status
    assert batch.attempt_count == 2
    assert batch.input_sha256 == INPUT_HASH
    assert _ReviewBatchPayload.model_validate_json(batch.model_dump_json()).root == batch
    match batch:
        case ModelReviewCompletedCheckpoint():
            assert "retry_not_before" not in batch.model_dump()
        case ModelReviewAttemptCheckpoint() | ModelReviewBackoffCheckpoint():
            pass


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"status": "unknown"},
        {"status": "backoff"},
        {"status": "backoff", "retry_not_before": "2026-11-14T00:00:00"},
        {"status": "attempted", "reviews": []},
        {"status": "completed", "reviews": []},
        {"status": "completed", "reviews": [], "retry_not_before": None},
        {"status": "attempted", "attempt_count": True},
        {"status": "attempted", "attempt_count": 0},
        {"status": "attempted", "attempt_count": 4},
        {"status": "attempted", "input_sha256": "unbound"},
    ],
)
def test_invalid_current_batch_states_are_unrepresentable(fields: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        _ReviewBatchPayload.model_validate(
            {"input_sha256": INPUT_HASH, "attempt_count": 1, **fields}
        )


async def test_current_checkpoint_requires_an_explicit_batch_discriminator() -> None:
    plan = await _saved_plan()
    with pytest.raises(ValidationError):
        ModelPlanningCheckpoint.model_validate(
            {
                **plan.model_dump(),
                "review_batches": ({"input_sha256": INPUT_HASH, "attempt_count": 1},),
            }
        )
