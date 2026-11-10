from pydantic import ValidationError
import pytest

from app.document_transform.native_date_flow import NativeDateParagraph
from app.document_transform.schemas import (
    InPlaceCheckpointPayload,
    InPlaceCheckpointStage,
    WordCheckpointPayload,
    transition_in_place,
)
from tests.advanced_round6_support import in_place_checkpoint, word_checkpoint


@pytest.mark.parametrize("stage", ["created", "verified"])
def test_word_checkpoint_requires_durable_output_before_advancing(stage: str) -> None:
    payload = {**word_checkpoint().model_dump(), "stage": stage}
    with pytest.raises(ValidationError):
        WordCheckpointPayload.model_validate(payload)


def test_in_place_headers_require_a_complete_mutation_receipt() -> None:
    payload = {**in_place_checkpoint().model_dump(), "marker_name": None}
    with pytest.raises(ValidationError):
        InPlaceCheckpointPayload.model_validate(payload)


def test_completed_in_place_checkpoint_cannot_retain_mutation_requests() -> None:
    payload = {**in_place_checkpoint().model_dump(), "stage": "complete"}
    with pytest.raises(ValidationError):
        InPlaceCheckpointPayload.model_validate(payload)


def test_in_place_cannot_skip_unverified_content_transition() -> None:
    checkpoint = in_place_checkpoint()
    with pytest.raises(ValueError, match="Invalid in-place checkpoint transition"):
        transition_in_place(
            checkpoint,
            stage=InPlaceCheckpointStage.COMPLETE,
            current_revision_id=checkpoint.current_revision_id,
            requests=[],
            marker_name=None,
            marker_range=None,
            mutation_sha256=None,
        )


@pytest.mark.parametrize("stage", ["content", "complete"])
def test_legacy_noop_markers_do_not_authorize_a_mutation(stage: str) -> None:
    payload = {**in_place_checkpoint().model_dump(), "stage": stage, "requests": []}
    checkpoint = InPlaceCheckpointPayload.model_validate(payload).root
    assert not checkpoint.requests
    assert checkpoint.marker_name is None
    assert checkpoint.marker_range is None
    assert checkpoint.mutation_sha256 is None


def test_native_checkpoint_rejects_coerced_indices_at_the_persistence_boundary() -> None:
    with pytest.raises(ValidationError):
        NativeDateParagraph.model_validate(
            {
                "tab_id": "tab",
                "block_id": "block",
                "source_sha256": "a" * 64,
                "paragraph": {"startIndex": "1", "endIndex": 2, "paragraph": {"elements": []}},
            }
        )
