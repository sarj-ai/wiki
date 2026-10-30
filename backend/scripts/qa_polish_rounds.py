# Offline gate: python -m scripts.qa_polish_rounds ledger.json --artifacts-root DIR
# The corpus runner supplies real manifests and private PDF/PNG artifacts; an
# independent judge supplies scores and visible evidence locations. Re-polish
# readback supplies canonical state hashes excluding revision/timestamps only.
# These recorded observations are not independently authenticated provider facts.

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field
import typer

from app.identifiers import FileId
from scripts.qa_document_transform_corpus import (
    EvidenceManifest,
    QaError,
    _require_manifest_invariants,
    _require_result_binding,
)


type Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
type Score = Annotated[int, Field(strict=True, ge=0, le=4)]
type Identity = Annotated[str, Field(min_length=1)]

_GOOD_SCORE = 3
_ROUND_COUNT = 5


class StrictEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QualityScores(StrictEvidence):
    # 0 harmful, 1 weak, 2 serviceable, 3 good, 4 expert/appropriate.
    # Score native features for appropriateness, not decoration count.
    navigation: Score
    hierarchy: Score
    typography: Score
    native_elements: Score
    author_fidelity: Score

    def weighted(self) -> float:
        return (
            self.navigation * 25
            + self.hierarchy * 25
            + self.typography * 20
            + self.native_elements * 15
            + self.author_fidelity * 15
        ) / 100


class JudgeComparison(StrictEvidence):
    source_id: Identity
    judge_id: Identity
    # Seeded order hides source/output labels during scoring, then is decoded here.
    first_shown: Literal["source", "output"]
    source: QualityScores
    output: QualityScores
    screenshot_sha256: tuple[Digest, ...] = Field(min_length=2)
    visible_locations: tuple[Identity, ...] = Field(min_length=1)
    hard_failures: tuple[Identity, ...] = ()


class RepeatEvidence(StrictEvidence):
    source_id: Identity
    task_id: uuid.UUID
    output_id_before: Identity
    output_id_after: Identity
    release_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    # Must include body, native segments, tab IDs/names, title, short-link target,
    # protected structures and permissions; exclude volatile provider revision.
    canonical_state_before: Digest
    canonical_state_after: Digest
    observed_native_state: Literal[True]


class ImprovementRound(StrictEvidence):
    number: Annotated[int, Field(ge=1, le=5)]
    focus: Identity
    manifest: EvidenceManifest
    artifact_directory: Identity
    comparisons: tuple[JudgeComparison, ...]
    repeats: tuple[RepeatEvidence, ...]


class ImprovementLedger(StrictEvidence):
    schema_version: Literal[1] = 1
    seed: Identity
    private_folder_id: Identity
    expected_source_ids: tuple[Identity, ...] = Field(min_length=3)
    rounds: tuple[ImprovementRound, ...] = Field(max_length=5)


def blind_first(seed: str, round_number: int, source_id: str) -> Literal["source", "output"]:
    digest = hashlib.sha256(f"{seed}\0{round_number}\0{source_id}".encode()).digest()
    return "source" if digest[0] % 2 == 0 else "output"


def require_quality(comparison: JudgeComparison) -> None:
    if comparison.hard_failures:
        msg = "judge recorded a preservation or safety failure"
        raise QaError(msg)
    before, after = comparison.source, comparison.output
    if min(after.model_dump().values()) < _GOOD_SCORE:
        msg = "every output quality dimension must score at least three"
        raise QaError(msg)
    if any(after.model_dump()[key] < value for key, value in before.model_dump().items()):
        msg = "a per-document quality dimension regressed"
        raise QaError(msg)
    required_gain = 0.5 if before.weighted() < _GOOD_SCORE else 0
    if round(after.weighted() - before.weighted(), 4) < required_gain:
        msg = "initially weak documents require measurable improvement"
        raise QaError(msg)


def verify_ledger(ledger: ImprovementLedger, artifacts_root: Path) -> int:
    # Return the verified observation count, never an assumed five.
    expected = ledger.expected_source_ids
    if len(set(expected)) != len(expected):
        msg = "baseline corpus contains duplicate source IDs"
        raise QaError(msg)
    if [entry.number for entry in ledger.rounds] != list(range(1, len(ledger.rounds) + 1)):
        msg = "rounds must be consecutive and cannot be replayed"
        raise QaError(msg)
    releases: set[str] = set()
    tasks: set[str] = set()
    outputs: set[str] = set()
    baselines: dict[str, tuple[str, str, str]] = {}
    scores: dict[str, QualityScores] = {}
    previous_outputs: dict[str, QualityScores] = {}
    for entry in ledger.rounds:
        release = entry.manifest.expected_release_sha
        if release in releases:
            msg = "each fix and redeploy round requires a distinct release"
            raise QaError(msg)
        releases.add(release)
        _require_round(ledger, entry, artifacts_root)
        for source in entry.manifest.sources:
            baseline = (source.revision_id, source.semantic_sha256, source.permission_sha256)
            if baselines.setdefault(str(source.source_id), baseline) != baseline:
                msg = "untouched source baseline changed between rounds"
                raise QaError(msg)
        improved_since_previous = False
        for judge in entry.comparisons:
            if scores.setdefault(judge.source_id, judge.source) != judge.source:
                msg = "baseline scores changed between rounds"
                raise QaError(msg)
            previous = previous_outputs.get(judge.source_id)
            if previous is not None:
                if any(
                    judge.output.model_dump()[key] < value
                    for key, value in previous.model_dump().items()
                ):
                    msg = "a later round regressed a previously achieved quality dimension"
                    raise QaError(msg)
                improved_since_previous |= judge.output.weighted() > previous.weighted()
            previous_outputs[judge.source_id] = judge.output
        if entry.number > 1 and not improved_since_previous:
            msg = "a new release alone is not an evidenced improvement round"
            raise QaError(msg)
        for result, repeat in zip(entry.manifest.results, entry.repeats, strict=True):
            for task_id in (str(result.task_id), str(repeat.task_id)):
                if task_id in tasks:
                    msg = "a task was counted more than once"
                    raise QaError(msg)
                tasks.add(task_id)
            if str(result.output_id) in outputs or str(result.output_id) in expected:
                msg = "regeneration reused an output or modified a baseline"
                raise QaError(msg)
            outputs.add(str(result.output_id))
    return len(ledger.rounds)


def _require_round(
    ledger: ImprovementLedger, entry: ImprovementRound, artifacts_root: Path
) -> None:
    manifest = entry.manifest
    _require_manifest_invariants(manifest)
    expected = set(ledger.expected_source_ids)
    for values in (
        [source.source_id for source in manifest.sources],
        [result.source_id for result in manifest.results],
        [judge.source_id for judge in entry.comparisons],
        [repeat.source_id for repeat in entry.repeats],
    ):
        if len(values) != len(expected) or set(values) != expected:
            msg = "round corpus is missing, duplicated, or changed"
            raise QaError(msg)
    artifact_directory = artifacts_root / entry.artifact_directory
    if not artifact_directory.resolve().is_relative_to(artifacts_root.resolve()):
        msg = "round artifacts escape their root"
        raise QaError(msg)
    judges = {judge.source_id: judge for judge in entry.comparisons}
    repeats = {repeat.source_id: repeat for repeat in entry.repeats}
    for source, result in zip(manifest.sources, manifest.results, strict=True):
        _require_result_binding(
            source,
            result,
            destination_folder_id=FileId(ledger.private_folder_id),
            expected_release_sha=manifest.expected_release_sha,
            artifacts_dir=artifact_directory,
            renderer_version=None,
            judge_model=manifest.judge_model,
            user_email=str(manifest.user_email),
        )
        if str(result.output_parent_id) != ledger.private_folder_id:
            msg = "output is not inside the bound private corpus folder"
            raise QaError(msg)
        judge = judges[str(source.source_id)]
        expected_hashes = tuple(
            artifact.sha256
            for artifact in (
                *result.visual_evidence.source_screenshots,
                *result.visual_evidence.output_screenshots,
            )
        )
        if judge.screenshot_sha256 != expected_hashes:
            msg = "judge must inspect every source and output screenshot"
            raise QaError(msg)
        if judge.first_shown != blind_first(ledger.seed, entry.number, str(source.source_id)):
            msg = "judge presentation order is not bound to the ledger seed"
            raise QaError(msg)
        require_quality(judge)
        repeat = repeats[str(source.source_id)]
        if not (
            repeat.output_id_before == repeat.output_id_after == str(result.output_id)
            and repeat.release_sha == manifest.expected_release_sha
            and repeat.canonical_state_before == repeat.canonical_state_after
        ):
            msg = "repeat polish changed identity, release, or canonical state"
            raise QaError(msg)


CLI = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=False,
)


@CLI.command()
def main(
    ledger: Annotated[Path, typer.Argument()],
    artifacts_root: Annotated[Path, typer.Option("--artifacts-root")],
) -> None:
    evidence = ImprovementLedger.model_validate_json(Path.read_text(ledger, encoding="utf-8"))
    completed = verify_ledger(evidence, artifacts_root)
    if completed != _ROUND_COUNT:
        msg = f"INCOMPLETE: {completed}/5 evidenced improvement rounds"
        typer.echo(msg, err=True)
        raise typer.Exit(1)
    print("PASS: 5/5 evidenced improvement rounds")


if __name__ == "__main__":
    CLI()
