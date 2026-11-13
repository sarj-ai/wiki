import json
from typing import Literal

from loguru import logger
import pytest

from app.document_transform import models as transform_models
from app.document_transform.ir import BlockKind
from app.document_transform.model_diagnostics import ModelBindingError, ModelCoverageError
from app.document_transform.schemas import PatchReviewEnvelope, TextPatchReview


@pytest.mark.parametrize(
    "mismatch", ["unknown_id", "duplicate_id", "source_hash", "replacement_hash", "missing_ids"]
)
def test_invalid_review_still_fails_with_precise_content_free_binding_evidence(
    mismatch: Literal[
        "unknown_id", "duplicate_id", "source_hash", "replacement_hash", "missing_ids"
    ],
) -> None:
    pair = transform_models._ReviewPair(
        block_id="PRIVATE BLOCK",
        expected_sha256="a" * 64,
        replacement_sha256="b" * 64,
        source="PRIVATE SOURCE",
        proposed="PRIVATE PROPOSAL",
        block_kind=BlockKind.PARAGRAPH,
    )
    valid = TextPatchReview(
        block_id=pair.block_id,
        expected_sha256=pair.expected_sha256,
        replacement_sha256=pair.replacement_sha256,
        equivalent=True,
        reason="PRIVATE PROVIDER REASON",
    )
    reviews = [valid]
    match mismatch:
        case "unknown_id":
            reviews = [valid.model_copy(update={"block_id": "PRIVATE UNEXPECTED"})]
        case "duplicate_id":
            reviews.append(valid)
        case "source_hash":
            reviews = [valid.model_copy(update={"expected_sha256": "c" * 64})]
        case "replacement_hash":
            reviews = [valid.model_copy(update={"replacement_sha256": "c" * 64})]
        case "missing_ids":
            reviews = []
    captured: list[str] = []
    sink = logger.add(captured.append, serialize=True)
    try:
        with pytest.raises(ModelCoverageError if mismatch == "missing_ids" else ModelBindingError):
            transform_models._validated_reviews([pair], PatchReviewEnvelope(reviews=reviews))
    finally:
        logger.remove(sink)
    assert captured
    extra = json.loads(captured[-1])["record"]["extra"]
    assert extra["model_binding_mismatch"] == mismatch
    assert extra["model_expected_review_count"] == 1
    assert extra["model_observed_review_count"] == len(reviews)
    assert "PRIVATE" not in "".join(captured)
    assert "a" * 64 not in "".join(captured)
    assert "b" * 64 not in "".join(captured)
