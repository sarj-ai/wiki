from collections.abc import Awaitable, Callable
import hashlib
import json
from typing import override

from app.document_transform.errors import OutputConflictError, SourceChangedError
from app.document_transform.ir import DocumentIR
from app.document_transform.models import PatchGenerator, RewriteReview, checkpointed_writer_scope
from app.document_transform.readability_types import ReadabilityProposal
from app.document_transform.schemas import (
    DocumentTransformRequest,
    ModelPlanningCheckpoint,
    TextPatch,
    TransformFinding,
    TransformPhase,
)
from app.document_transform.unslop import UNSLOP_CATALOG_SHA256
from app.document_transform.versions import DOCUMENT_PIPELINE_VERSION
from app.identifiers import ModelId


class ModelReviewPendingError(Exception):
    pass


def _request_sha256(request: DocumentTransformRequest) -> str:
    return hashlib.sha256(
        json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
        self._checkpoint = checkpoint
        self._save = save
        self._used_initial = False
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
        if phase is not TransformPhase.UNSLOP or self._used_initial:
            return await self._generator.generate(document, phase=phase)
        self._used_initial = True
        if self._checkpoint is not None:
            if document.text_sha256 != self._checkpoint.document_sha256:
                raise SourceChangedError
            return [patch.model_copy(deep=True) for patch in self._checkpoint.proposals]
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
        raise ModelReviewPendingError

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        return await self._generator.review(document, patches)

    @override
    async def repair(
        self,
        document: DocumentIR,
        patches: list[TextPatch],
        findings: tuple[TransformFinding, ...],
        /,
    ) -> list[TextPatch]:
        return await self._generator.repair(document, patches, findings)

    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        return await self._generator.plan_readability(
            document, previous=previous, feedback=feedback
        )
