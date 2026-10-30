from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, override
import uuid

from app.document_transform.errors import (
    DocumentGatewayError,
    OutputConflictError,
    SourceChangedError,
    UnsupportedSourceError,
)
from app.document_transform.executor import DocumentTransformCheckpointStore
from app.document_transform.naming import title_case_document_name
from app.document_transform.runner import DocumentTransformRunner
from app.document_transform.schemas import (
    CopyCheckpointStage,
    DocumentTransformCheckpoint,
    DocumentTransformRequest,
    DocumentTransformResult,
    DriveFileSource,
    OutputStrategy,
    TransformFinding,
    TransformMode,
    ValidationSummary,
    WordCopyCheckpoint,
)
from app.document_transform.versions import WORD_PIPELINE_VERSION
from app.document_transform.word_gateway import WORD_MIME, WordGateway
from app.document_transform.word_package import (
    TARGET_FONT,
    UnsafeWordPackageError,
    format_word_package,
)
from app.identifiers import TaskId


if TYPE_CHECKING:
    from app.document_transform.schemas import DocumentOutput
    from app.document_transform.word_gateway import WordSource
    from app.document_transform.word_package import WordFormatResult


_MAX_OUTPUT_STEM = 250


class WordTransformExecutor(DocumentTransformRunner):
    def __init__(
        self,
        *,
        gateway: WordGateway,
        checkpoint_store: DocumentTransformCheckpointStore,
        task_id: TaskId,
        lease_token: uuid.UUID,
        checkpoint: DocumentTransformCheckpoint | None,
        checkpoint_generation: int,
    ) -> None:
        self._gateway = gateway
        self._store = checkpoint_store
        self._task_id = task_id
        self._lease_token = lease_token
        self._checkpoint = checkpoint
        self._generation = checkpoint_generation

    @override
    async def execute(
        self, *, operation_id: uuid.UUID, request: DocumentTransformRequest
    ) -> DocumentTransformResult:
        if (
            request.mode is not TransformMode.FORMAT
            or request.output_strategy is not OutputStrategy.COPY
            or not isinstance(request.source, DriveFileSource)
        ):
            raise UnsupportedSourceError
        if operation_id != self._task_id:
            raise OutputConflictError
        source = await self._gateway.load_word(request.source)
        try:
            formatted = format_word_package(source.content)
        except UnsafeWordPackageError as error:
            raise UnsupportedSourceError from error
        checkpoint = await self._checkpoint_for_request(request, source, formatted)
        checkpoint, recovered = await self._recover_or_create(
            operation_id, checkpoint, source, formatted
        )
        await self._gateway.verify_word(
            recovered, expected=formatted.content, operation_id=operation_id, source=source
        )
        latest = await self._gateway.load_word(request.source)
        if latest.binding != source.binding or latest.content != source.content:
            raise SourceChangedError
        if checkpoint.stage is CopyCheckpointStage.VERIFIED:
            if checkpoint.result is None:
                raise OutputConflictError
            expected = self._result(operation_id, checkpoint)
            if checkpoint.result != expected:
                raise OutputConflictError
            return checkpoint.result
        result = self._result(operation_id, checkpoint)
        await self._save(
            checkpoint.model_copy(update={"stage": CopyCheckpointStage.VERIFIED, "result": result})
        )
        return result

    async def _checkpoint_for_request(
        self,
        request: DocumentTransformRequest,
        source: WordSource,
        formatted: WordFormatResult,
    ) -> WordCopyCheckpoint:
        request_sha256 = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        checkpoint = self._checkpoint
        if checkpoint is not None and not isinstance(checkpoint, WordCopyCheckpoint):
            raise OutputConflictError
        if checkpoint is not None:
            try:
                checkpoint = WordCopyCheckpoint.model_validate(checkpoint.model_dump())
            except ValueError as error:
                raise OutputConflictError from error
        if checkpoint is None:
            checkpoint = WordCopyCheckpoint(
                request_sha256=request_sha256,
                source_binding=source.binding,
                source_sha256=formatted.source_sha256,
                output_sha256=formatted.output_sha256,
                transformer_version=WORD_PIPELINE_VERSION,
                target_font=TARGET_FONT,
                output_name=_output_name(request.output_name, source.binding["name"]),
                reserved_output_id=await self._gateway.reserve_word_id(),
                changed_run_count=formatted.changed_runs,
                stage=CopyCheckpointStage.PLANNED,
            )
        elif any(
            (
                checkpoint.request_sha256 != request_sha256,
                checkpoint.source_binding != source.binding,
                checkpoint.source_sha256 != formatted.source_sha256,
                checkpoint.output_sha256 != formatted.output_sha256,
                checkpoint.transformer_version != WORD_PIPELINE_VERSION,
                checkpoint.target_font != TARGET_FONT,
                checkpoint.changed_run_count != formatted.changed_runs,
                checkpoint.output_name != _output_name(request.output_name, source.binding["name"]),
            )
        ):
            raise OutputConflictError
        return checkpoint

    async def _recover_or_create(
        self,
        operation_id: uuid.UUID,
        checkpoint: WordCopyCheckpoint,
        source: WordSource,
        formatted: WordFormatResult,
    ) -> tuple[WordCopyCheckpoint, DocumentOutput]:
        recovered = await self._gateway.recover_word(
            operation_id,
            source=source,
            output_sha256=formatted.output_sha256,
            output_id=checkpoint.reserved_output_id,
        )
        if self._checkpoint is None:
            if recovered is not None:
                raise OutputConflictError
            await self._save(checkpoint)
        if recovered is None:
            if checkpoint.stage not in {CopyCheckpointStage.PLANNED, CopyCheckpointStage.CREATING}:
                raise DocumentGatewayError(retryable=True)
            if checkpoint.stage is CopyCheckpointStage.PLANNED:
                checkpoint = checkpoint.model_copy(update={"stage": CopyCheckpointStage.CREATING})
                await self._save(checkpoint)
            # Drive guarantees same-ID retries return 409 rather than creating duplicates.
            recovered = await self._gateway.create_word(
                operation_id,
                source=source,
                content=formatted.content,
                name=checkpoint.output_name,
                output_id=checkpoint.reserved_output_id,
            )
        elif checkpoint.stage is CopyCheckpointStage.PLANNED:
            raise OutputConflictError
        if (
            recovered.name != checkpoint.output_name
            or recovered.media_type != WORD_MIME
            or recovered.file_id != checkpoint.reserved_output_id
        ):
            raise OutputConflictError
        if checkpoint.output is not None and checkpoint.output != recovered:
            raise OutputConflictError
        if checkpoint.stage is CopyCheckpointStage.CREATING:
            checkpoint = checkpoint.model_copy(
                update={"stage": CopyCheckpointStage.CREATED, "output": recovered}
            )
            await self._save(checkpoint)
        return checkpoint, recovered

    async def _save(self, checkpoint: WordCopyCheckpoint) -> None:
        self._generation = await self._store.save_document_transform_checkpoint(
            self._task_id,
            lease_token=self._lease_token,
            expected_generation=self._generation,
            checkpoint=checkpoint,
        )
        self._checkpoint = checkpoint

    @staticmethod
    def _result(operation_id: uuid.UUID, checkpoint: WordCopyCheckpoint) -> DocumentTransformResult:
        if checkpoint.output is None:
            raise OutputConflictError
        return DocumentTransformResult(
            operation_id=operation_id,
            source_sha256=checkpoint.source_sha256,
            output=checkpoint.output,
            mode=TransformMode.FORMAT,
            applied_patch_count=checkpoint.changed_run_count,
            deterministic_patch_count=checkpoint.changed_run_count,
            findings=[
                TransformFinding(
                    code="word_native_format_preserved",
                    detail="Word format preserved. Only supported body font attributes changed; wording, tables, fields, headers and footers were retained. Review rendered pagination before publishing.",
                )
            ],
            validation=ValidationSummary(
                source_unchanged=True,
                protected_content_preserved=True,
                structure_preserved=True,
                unsafe_patch_count=0,
            ),
            model_ids=[],
            tool_versions={"word_package": WORD_PIPELINE_VERSION},
            pipeline_version=WORD_PIPELINE_VERSION,
            needs_review=True,
            changed=checkpoint.changed_run_count > 0,
        )


def _output_name(requested: str | None, source: str) -> str:
    if requested is not None:
        if len(requested) > _MAX_OUTPUT_STEM and not requested.casefold().endswith(".docx"):
            raise UnsupportedSourceError
        return requested if requested.casefold().endswith(".docx") else f"{requested}.docx"
    stem = source[:-5] if source.casefold().endswith(".docx") else source
    return f"{title_case_document_name(stem.strip())[:_MAX_OUTPUT_STEM]}.docx"
