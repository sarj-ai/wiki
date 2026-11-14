from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import math
from typing import assert_never

from app.document_transform.errors import OutputConflictError
from app.document_transform.schemas import (
    ModelReviewAttemptCheckpoint,
    ModelReviewBackoffCheckpoint,
    ModelReviewBatchCheckpoint,
    ModelReviewCompletedCheckpoint,
    PatchReviewEnvelope,
)
from app.identifiers import ModelReviewInputHash


@dataclass(frozen=True, slots=True)
class ReviewCheckpointStore:
    get: Callable[[ModelReviewInputHash], ModelReviewBatchCheckpoint | None]
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
        self._input_sha256 = ModelReviewInputHash(hashlib.sha256(prompt.encode()).hexdigest())
        self._checkpoint = self._store.get(self._input_sha256) if self._store is not None else None
        if self._checkpoint is not None and self._checkpoint.input_sha256 != self._input_sha256:
            raise OutputConflictError

    @property
    def durable(self) -> bool:
        return self._store is not None

    @property
    def attempt_count(self) -> int:
        return self._checkpoint.attempt_count if self._checkpoint is not None else 0

    @property
    def reviews(self) -> PatchReviewEnvelope | None:
        match self._checkpoint:
            case ModelReviewCompletedCheckpoint(reviews=reviews):
                return PatchReviewEnvelope(reviews=list(reviews))
            case ModelReviewAttemptCheckpoint() | ModelReviewBackoffCheckpoint() | None:
                return None
            case unreachable:
                assert_never(unreachable)

    @property
    def retry_delay(self) -> float:
        match self._checkpoint:
            case ModelReviewBackoffCheckpoint(retry_not_before=deadline):
                return max((deadline - datetime.now(UTC)).total_seconds(), 0)
            case ModelReviewAttemptCheckpoint() | ModelReviewCompletedCheckpoint() | None:
                return 0
            case unreachable:
                assert_never(unreachable)

    async def begin(self, attempt: int) -> None:
        if isinstance(self._checkpoint, ModelReviewCompletedCheckpoint):
            raise OutputConflictError
        if attempt != self.attempt_count + 1:
            raise OutputConflictError
        await self._save(
            ModelReviewAttemptCheckpoint(input_sha256=self._input_sha256, attempt_count=attempt)
        )

    async def defer(self, attempt: int, delay: float) -> None:
        if not math.isfinite(delay) or delay < 0:
            msg = "Review retry delay must be finite and nonnegative"
            raise ValueError(msg)
        match self._require_checkpoint(attempt):
            case ModelReviewAttemptCheckpoint() as checkpoint:
                await self._save(
                    ModelReviewBackoffCheckpoint(
                        input_sha256=checkpoint.input_sha256,
                        attempt_count=checkpoint.attempt_count,
                        retry_not_before=datetime.now(UTC) + timedelta(seconds=delay),
                    )
                )
            case ModelReviewBackoffCheckpoint():
                return
            case ModelReviewCompletedCheckpoint():
                raise OutputConflictError
            case unreachable:
                assert_never(unreachable)

    async def complete(self, attempt: int, reviews: PatchReviewEnvelope) -> None:
        match self._require_checkpoint(attempt):
            case ModelReviewAttemptCheckpoint() as checkpoint:
                await self._save(
                    ModelReviewCompletedCheckpoint(
                        input_sha256=checkpoint.input_sha256,
                        attempt_count=checkpoint.attempt_count,
                        reviews=tuple(reviews.reviews),
                    )
                )
            case ModelReviewCompletedCheckpoint() as checkpoint:
                if checkpoint.reviews != tuple(reviews.reviews):
                    raise OutputConflictError
            case ModelReviewBackoffCheckpoint():
                raise OutputConflictError
            case unreachable:
                assert_never(unreachable)

    def _require_checkpoint(self, attempt: int) -> ModelReviewBatchCheckpoint:
        if self._checkpoint is None or self._checkpoint.attempt_count != attempt:
            raise OutputConflictError
        return self._checkpoint

    async def _save(self, checkpoint: ModelReviewBatchCheckpoint) -> None:
        if self._store is not None:
            await self._store.save(checkpoint)
        self._checkpoint = checkpoint
