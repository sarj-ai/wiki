from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib

from app.document_transform.errors import OutputConflictError
from app.document_transform.schemas import ModelReviewBatchCheckpoint, PatchReviewEnvelope


@dataclass(frozen=True, slots=True)
class ReviewCheckpointStore:
    get: Callable[[str], ModelReviewBatchCheckpoint | None]
    save: Callable[[ModelReviewBatchCheckpoint], Awaitable[None]]


_store: ContextVar[ReviewCheckpointStore | None] = ContextVar(
    "document_semantic_review_checkpoint", default=None
)


@contextmanager
def checkpointed_review_scope(store: ReviewCheckpointStore) -> Generator[None]:
    token = _store.set(store)
    try:
        yield
    finally:
        _store.reset(token)


def review_checkpoint_store() -> ReviewCheckpointStore | None:
    return _store.get()


class ReviewBatchProgress:
    def __init__(self, prompt: str) -> None:
        self._store = review_checkpoint_store()
        self._input_sha256 = hashlib.sha256(prompt.encode()).hexdigest()
        self._checkpoint = self._store.get(self._input_sha256) if self._store is not None else None

    @property
    def durable(self) -> bool:
        return self._store is not None

    @property
    def attempt_count(self) -> int:
        return self._checkpoint.attempt_count if self._checkpoint is not None else 0

    @property
    def reviews(self) -> PatchReviewEnvelope | None:
        if self._checkpoint is not None and self._checkpoint.reviews:
            return PatchReviewEnvelope(reviews=list(self._checkpoint.reviews))
        return None

    @property
    def retry_delay(self) -> float:
        if self._checkpoint is None or self._checkpoint.retry_not_before is None:
            return 0
        return max((self._checkpoint.retry_not_before - datetime.now(UTC)).total_seconds(), 0)

    async def begin(self, attempt: int) -> None:
        await self._save(
            ModelReviewBatchCheckpoint(input_sha256=self._input_sha256, attempt_count=attempt)
        )

    async def defer(self, delay: float) -> None:
        checkpoint = self._require_checkpoint()
        await self._save(
            checkpoint.model_copy(
                update={"retry_not_before": datetime.now(UTC) + timedelta(seconds=delay)}
            )
        )

    async def complete(self, reviews: PatchReviewEnvelope) -> None:
        checkpoint = self._require_checkpoint()
        await self._save(checkpoint.model_copy(update={"reviews": tuple(reviews.reviews)}))

    def _require_checkpoint(self) -> ModelReviewBatchCheckpoint:
        if self._checkpoint is None:
            raise OutputConflictError
        return self._checkpoint

    async def _save(self, checkpoint: ModelReviewBatchCheckpoint) -> None:
        if self._store is not None:
            await self._store.save(checkpoint)
        self._checkpoint = checkpoint
