from pathlib import Path

from pydantic import ValidationError
import pytest

from scripts.qa_document_transform_corpus import QaError
from scripts.qa_polish_rounds import (
    ImprovementLedger,
    JudgeComparison,
    QualityScores,
    RepeatEvidence,
    blind_first,
    require_quality,
    verify_ledger,
)


def _scores(value: int) -> QualityScores:
    return QualityScores(
        navigation=value,
        hierarchy=value,
        typography=value,
        native_elements=value,
        author_fidelity=value,
    )


def _comparison(before: QualityScores, after: QualityScores) -> JudgeComparison:
    return JudgeComparison(
        source_id="source-a",
        judge_id="independent-judge",
        first_shown="source",
        source=before,
        output=after,
        screenshot_sha256=("a" * 64, "b" * 64),
        visible_locations=("Output page 2: section heading remains with its paragraph",),
    )


def test_weak_document_must_improve_and_meet_every_dimension_floor() -> None:
    require_quality(_comparison(_scores(2), _scores(3)))
    with pytest.raises(QaError, match="every output"):
        require_quality(_comparison(_scores(2), _scores(2)))


def test_small_gain_on_initially_weak_input_is_not_counted_as_useful() -> None:
    before = _scores(3).model_copy(update={"native_elements": 2})
    with pytest.raises(QaError, match="measurable improvement"):
        require_quality(_comparison(before, _scores(3)))


def test_strong_document_may_tie_without_cosmetic_churn() -> None:
    require_quality(_comparison(_scores(4), _scores(4)))


def test_aggregate_gain_cannot_hide_a_dimension_regression() -> None:
    before = _scores(2).model_copy(update={"author_fidelity": 4})
    with pytest.raises(QaError, match="dimension regressed"):
        require_quality(_comparison(before, _scores(3)))


def test_pretty_unsafe_result_never_passes() -> None:
    comparison = _comparison(_scores(1), _scores(4)).model_copy(
        update={"hard_failures": ("lost hyperlink destination",)}
    )
    with pytest.raises(QaError, match="safety failure"):
        require_quality(comparison)


def test_score_rejects_boolean_and_out_of_range() -> None:
    for invalid in (True, 5, -1, 3.1):
        with pytest.raises(ValidationError):
            QualityScores.model_validate({**_scores(3).model_dump(), "typography": invalid})


def test_blind_order_is_stable_and_uses_both_presentations() -> None:
    orders = [blind_first("seed", 1, f"document-{index}") for index in range(30)]
    assert set(orders) == {"source", "output"}
    assert orders == [blind_first("seed", 1, f"document-{index}") for index in range(30)]


def test_no_observations_is_zero_rounds_not_five(tmp_path: Path) -> None:
    ledger = ImprovementLedger(
        seed="seed",
        private_folder_id="private-folder",
        expected_source_ids=("source-a", "source-b", "source-c"),
        rounds=(),
    )
    assert verify_ledger(ledger, tmp_path) == 0
    with pytest.raises(QaError, match="duplicate source"):
        verify_ledger(ledger.model_copy(update={"expected_source_ids": ("a", "a", "b")}), tmp_path)


def test_judge_needs_actual_screenshot_hashes_and_visible_locations() -> None:
    comparison = _comparison(_scores(2), _scores(3)).model_dump()
    for update in ({"screenshot_sha256": ()}, {"visible_locations": ()}):
        with pytest.raises(ValidationError):
            JudgeComparison.model_validate({**comparison, **update})


def test_repeat_requires_native_observation_not_visual_similarity() -> None:
    with pytest.raises(ValidationError):
        RepeatEvidence.model_validate(
            {
                "source_id": "source-a",
                "task_id": "00000000-0000-0000-0000-000000000001",
                "output_id_before": "output-a",
                "output_id_after": "output-a",
                "release_sha": "a" * 40,
                "canonical_state_before": "b" * 64,
                "canonical_state_after": "b" * 64,
                "observed_native_state": False,
            }
        )
