from pydantic import BaseModel

from app.document_transform.errors import OutputConflictError
from app.document_transform.schemas import (
    CopyCheckpointStage,
    CreatedCopyCheckpoint,
    CreatedWordCheckpoint,
    CreatingCopyCheckpoint,
    CreatingWordCheckpoint,
    DocumentOutput,
    DocumentTransformResult,
    PlannedCopyCheckpoint,
    PlannedWordCheckpoint,
    VerifiedCopyCheckpoint,
    VerifiedWordCheckpoint,
)
from app.identifiers import DocsRevisionId


def begin_copy(checkpoint: PlannedCopyCheckpoint) -> CreatingCopyCheckpoint:
    _require_state(checkpoint, PlannedCopyCheckpoint)
    return CreatingCopyCheckpoint.model_validate(
        {**checkpoint.model_dump(), "stage": CopyCheckpointStage.CREATING}
    )


def record_copy(
    checkpoint: CreatingCopyCheckpoint, output: DocumentOutput, revision: DocsRevisionId
) -> CreatedCopyCheckpoint:
    _require_state(checkpoint, CreatingCopyCheckpoint)
    return CreatedCopyCheckpoint.model_validate(
        {
            **checkpoint.model_dump(),
            "stage": CopyCheckpointStage.CREATED,
            "output": output,
            "output_revision_id": revision,
        }
    )


def verify_copy(
    checkpoint: CreatedCopyCheckpoint,
    output: DocumentOutput,
    revision: DocsRevisionId,
    result: DocumentTransformResult,
) -> VerifiedCopyCheckpoint:
    _require_state(checkpoint, CreatedCopyCheckpoint)
    return VerifiedCopyCheckpoint.model_validate(
        {
            **checkpoint.model_dump(),
            "stage": CopyCheckpointStage.VERIFIED,
            "output": output,
            "output_revision_id": revision,
            "result": result,
        }
    )


def begin_word(checkpoint: PlannedWordCheckpoint) -> CreatingWordCheckpoint:
    _require_state(checkpoint, PlannedWordCheckpoint)
    return CreatingWordCheckpoint.model_validate(
        {**checkpoint.model_dump(), "stage": CopyCheckpointStage.CREATING}
    )


def record_word(
    checkpoint: CreatingWordCheckpoint, output: DocumentOutput
) -> CreatedWordCheckpoint:
    _require_state(checkpoint, CreatingWordCheckpoint)
    return CreatedWordCheckpoint.model_validate(
        {**checkpoint.model_dump(), "stage": CopyCheckpointStage.CREATED, "output": output}
    )


def verify_word(
    checkpoint: CreatedWordCheckpoint, result: DocumentTransformResult
) -> VerifiedWordCheckpoint:
    _require_state(checkpoint, CreatedWordCheckpoint)
    return VerifiedWordCheckpoint.model_validate(
        {**checkpoint.model_dump(), "stage": CopyCheckpointStage.VERIFIED, "result": result}
    )


def _require_state(value: object, expected: type[BaseModel]) -> None:
    if not isinstance(value, expected):
        raise OutputConflictError
