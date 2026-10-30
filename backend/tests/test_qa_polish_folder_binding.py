from pathlib import Path

from pydantic import ValidationError
import pytest

from scripts.qa_document_transform_corpus import QaError
from scripts.qa_polish_rounds import (
    ImprovementLedger,
    ImprovementRound,
    JudgeComparison,
    QualityScores,
    RepeatEvidence,
    _require_round,
    blind_first,
)
from tests.test_qa_document_transform_corpus import (
    _evidence_manifest,
    _evidence_result,
    _source_evidence,
    _visual_evidence,
)


@pytest.mark.parametrize(
    "source_ids", [(), ("source-a",), ("source-a", "source-b")], ids=["empty", "one", "two"]
)
def test_public_ledger_rejects_undersized_corpus(source_ids: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="at least 3"):
        ImprovementLedger(
            seed="seed", private_folder_id="destination", expected_source_ids=source_ids, rounds=()
        )


@pytest.mark.parametrize(
    "folder", ["destination", "different-folder"], ids=["bound", "wrong-folder"]
)
def test_round_binds_result_to_ledger_folder(folder: str, tmp_path: Path) -> None:
    source = _source_evidence()
    evidence = _visual_evidence(source=source, artifacts_dir=tmp_path / "round-1")
    result = _evidence_result(
        expected_release_sha="a" * 40, source=source, visual_evidence=evidence
    )
    scores = QualityScores(
        navigation=4, hierarchy=4, typography=4, native_elements=4, author_fidelity=4
    )
    comparison = JudgeComparison(
        source_id=str(source.source_id),
        judge_id="independent-judge",
        first_shown=blind_first("seed", 1, str(source.source_id)),
        source=scores,
        output=scores,
        screenshot_sha256=tuple(
            artifact.sha256
            for artifact in (*evidence.source_screenshots, *evidence.output_screenshots)
        ),
        visible_locations=("Output page 1: the source heading remains intact",),
    )
    repeat = RepeatEvidence(
        source_id=str(source.source_id),
        task_id=result.task_id,
        output_id_before=str(result.output_id),
        output_id_after=str(result.output_id),
        release_sha="a" * 40,
        canonical_state_before="b" * 64,
        canonical_state_after="b" * 64,
        observed_native_state=True,
    )
    entry = ImprovementRound(
        number=1,
        focus="preserve heading placement",
        manifest=_evidence_manifest(source=source, results=(result,)),
        artifact_directory="round-1",
        comparisons=(comparison,),
        repeats=(repeat,),
    )
    # Isolate per-round binding with one source; public-ledger cardinality is tested separately.
    ledger = ImprovementLedger.model_construct(
        seed="seed",
        private_folder_id=folder,
        expected_source_ids=(str(source.source_id),),
        rounds=(entry,),
    )
    if folder == "destination":
        _require_round(ledger, entry, tmp_path)
    else:
        with pytest.raises(QaError, match="bound to its source"):
            _require_round(ledger, entry, tmp_path)
