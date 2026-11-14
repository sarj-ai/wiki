import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
from typing import ClassVar, Literal, assert_never, override

from app.document_transform.errors import (
    ModelDeadlineError,
    OutputConflictError,
    SourceChangedError,
)
from app.document_transform.ir import DocumentIR
from app.document_transform.models import PatchGenerator, RewriteReview, checkpointed_writer_scope
from app.document_transform.readability_types import ReadabilityProposal
from app.document_transform.review_checkpoint import (
    ReviewCheckpointStore,
    checkpointed_review_scope,
)
from app.document_transform.schemas import (
    DocumentTransformRequest,
    ModelPlanningCheckpoint,
    ModelReviewBatchCheckpoint,
    TextPatch,
    TransformFinding,
    TransformPhase,
)
from app.document_transform.unslop import UNSLOP_CATALOG_SHA256
from app.document_transform.versions import DOCUMENT_PIPELINE_VERSION
from app.identifiers import ModelId, ModelReviewInputHash
from app.performance import provider_retry_remaining_seconds


class ModelReviewPendingError(Exception):
    pass


def _request_sha256(request: DocumentTransformRequest) -> str:
    return hashlib.sha256(
        json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class _FreshPlan:
    kind: ClassVar[Literal["fresh"]] = "fresh"


@dataclass(frozen=True, slots=True)
class _PreparingPlan:
    kind: ClassVar[Literal["preparing"]] = "preparing"


@dataclass(frozen=True, slots=True)
class _SavedPlan:
    kind: ClassVar[Literal["saved"]] = "saved"
    checkpoint: ModelPlanningCheckpoint


@dataclass(frozen=True, slots=True)
class _AwaitingReview:
    kind: ClassVar[Literal["awaiting_review"]] = "awaiting_review"
    checkpoint: ModelPlanningCheckpoint


@dataclass(frozen=True, slots=True)
class _Reviewing:
    kind: ClassVar[Literal["reviewing"]] = "reviewing"
    checkpoint: ModelPlanningCheckpoint


@dataclass(frozen=True, slots=True)
class _LaterPass:
    kind: ClassVar[Literal["later_pass"]] = "later_pass"


@dataclass(frozen=True, slots=True)
class _InterruptedPlan:
    kind: ClassVar[Literal["interrupted"]] = "interrupted"


type _PlanningState = (
    _FreshPlan
    | _PreparingPlan
    | _SavedPlan
    | _AwaitingReview
    | _Reviewing
    | _LaterPass
    | _InterruptedPlan
)


class CheckpointingPatchGenerator(PatchGenerator):
    def __init__(
        self,
        *,
        generator: PatchGenerator,
        request: DocumentTransformRequest,
        checkpoint: ModelPlanningCheckpoint | None,
        save: Callable[[ModelPlanningCheckpoint], Awaitable[None]],
    ) -> None:
        self._generator = generator
        self._request_sha256 = _request_sha256(request)
        self._state: _PlanningState = _FreshPlan() if checkpoint is None else _SavedPlan(checkpoint)
        self._save = save
        self._review_save_lock = asyncio.Lock()
        self.supports_semantic_rewrite = generator.supports_semantic_rewrite
        self.supports_readability_planning = generator.supports_readability_planning
        if checkpoint is not None and (
            checkpoint.request_sha256 != self._request_sha256
            or checkpoint.pipeline_version != DOCUMENT_PIPELINE_VERSION
            or checkpoint.catalog_sha256 != UNSLOP_CATALOG_SHA256
            or checkpoint.model_ids != tuple(ModelId(value) for value in generator.model_ids)
        ):
            raise OutputConflictError

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return self._generator.model_ids

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        if phase is not TransformPhase.UNSLOP:
            raise OutputConflictError
        match self._state:
            case _SavedPlan(checkpoint=checkpoint):
                if document.text_sha256 != checkpoint.document_sha256:
                    raise SourceChangedError
                self._state = _AwaitingReview(checkpoint)
                return [patch.model_copy(deep=True) for patch in checkpoint.proposals]
            case _LaterPass():
                return await self._generator.generate(document, phase=phase)
            case _FreshPlan():
                self._state = _PreparingPlan()
                try:
                    with checkpointed_writer_scope():
                        proposals = await self._generator.generate(document, phase=phase)
                    checkpoint = ModelPlanningCheckpoint(
                        request_sha256=self._request_sha256,
                        document_sha256=document.text_sha256,
                        pipeline_version=DOCUMENT_PIPELINE_VERSION,
                        catalog_sha256=UNSLOP_CATALOG_SHA256,
                        model_ids=tuple(ModelId(value) for value in self.model_ids),
                        proposals=tuple(proposals),
                    )
                    await self._save(checkpoint)
                except BaseException:
                    self._state = _InterruptedPlan()
                    raise
                self._state = _SavedPlan(checkpoint)
                raise ModelReviewPendingError
            case _PreparingPlan() | _AwaitingReview() | _Reviewing() | _InterruptedPlan():
                raise OutputConflictError
            case unreachable:
                assert_never(unreachable)

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        match self._state:
            case _LaterPass():
                return await self._generator.review(document, patches)
            case _SavedPlan(checkpoint=checkpoint) | _AwaitingReview(checkpoint=checkpoint):
                # Review binds immutable original prose, which can differ from
                # the deterministically cleaned document used to plan patches.
                self._state = _Reviewing(checkpoint)
                store = ReviewCheckpointStore(get=self._review_batch, save=self._save_review_batch)
                try:
                    with checkpointed_review_scope(store):
                        decision = await self._generator.review(document, patches)
                except ModelDeadlineError as error:
                    batches = self._require_review_checkpoint().review_batches
                    self._state = _InterruptedPlan()
                    if batches:
                        raise ModelReviewPendingError from error
                    raise
                except BaseException:
                    self._state = _InterruptedPlan()
                    raise
                else:
                    self._state = _LaterPass()
                    return decision
            case _FreshPlan() | _PreparingPlan() | _Reviewing() | _InterruptedPlan():
                raise OutputConflictError
            case unreachable:
                assert_never(unreachable)

    def _require_review_checkpoint(self) -> ModelPlanningCheckpoint:
        if not isinstance(self._state, _Reviewing):
            raise OutputConflictError
        return self._state.checkpoint

    def _review_batch(
        self, input_sha256: ModelReviewInputHash
    ) -> ModelReviewBatchCheckpoint | None:
        checkpoint = self._require_review_checkpoint()
        return next(
            (batch for batch in checkpoint.review_batches if batch.input_sha256 == input_sha256),
            None,
        )

    async def _save_review_batch(self, batch: ModelReviewBatchCheckpoint) -> None:
        async with self._review_save_lock:
            checkpoint = self._require_review_checkpoint()
            batches = {item.input_sha256: item for item in checkpoint.review_batches}
            batches[batch.input_sha256] = batch
            updated = ModelPlanningCheckpoint.model_validate(
                {**checkpoint.model_dump(), "review_batches": tuple(batches.values())}
            )
            committing = asyncio.create_task(self._commit_review_checkpoint(updated))
            try:
                await asyncio.shield(committing)
            except asyncio.CancelledError:
                # Finish the bounded CAS/commit before releasing the lock or
                # allowing the worker to defer with this shared DB session.
                await committing
                raise

    async def _commit_review_checkpoint(self, checkpoint: ModelPlanningCheckpoint) -> None:
        remaining = provider_retry_remaining_seconds()
        timeout = 30.0 if remaining is None else min(30.0, max(remaining, 0))
        async with asyncio.timeout(timeout):
            self._require_review_checkpoint()
            await self._save(checkpoint)
        self._require_review_checkpoint()
        self._state = _Reviewing(checkpoint)

    @override
    async def repair(
        self,
        document: DocumentIR,
        patches: list[TextPatch],
        findings: tuple[TransformFinding, ...],
        /,
    ) -> list[TextPatch]:
        match self._state:
            case _AwaitingReview() | _LaterPass():
                self._state = _LaterPass()
                return await self._generator.repair(document, patches, findings)
            case _FreshPlan() | _PreparingPlan() | _SavedPlan() | _Reviewing() | _InterruptedPlan():
                raise OutputConflictError
            case unreachable:
                assert_never(unreachable)

    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        return await self._generator.plan_readability(
            document, previous=previous, feedback=feedback
        )
