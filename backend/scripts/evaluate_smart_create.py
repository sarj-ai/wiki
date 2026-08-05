"""Read-only, privacy-preserving evaluation of Smart Create folder placement."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from pathlib import Path
import random
import secrets
import time
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, Field

from app.config import settings
from app.drive.schemas import DriveSummary
from app.identifiers import DriveId, FileId
from app.smart_create.schemas import AIMode, ProposalDestination
from app.smart_create.service import (
    VertexProposalGenerator,
    smart_create_candidates,
    smart_create_destinations,
    validate_generated_proposal,
)
from scripts.evaluate_reorganization import build_personal_tree
from scripts.organize_my_drive import MIME_DOCUMENT, InventoryItem, load_drive_organizer


DEFAULT_CASES = 50
DEFAULT_MODELS = ("gemini-3.5-flash-lite", "gemini-3.5-flash")
DEFAULT_DEPTHS: tuple[int | None, ...] = (None, 1, 2, 3)
DEFAULT_THRESHOLDS = (0.6, 0.7, 0.8, 0.9)
DEFAULT_OUTPUT = Path(".drive-organizer/smart-create-eval.json")
MODEL_TIMEOUT_SECONDS = 30
MODEL_CONCURRENCY = 4
MIN_DESCRIPTION_CHARS = 3


class Strategy(BaseModel, frozen=True):
    model: str
    candidate_depth: int | None

    @property
    def key(self) -> str:
        depth = "unlimited" if self.candidate_depth is None else str(self.candidate_depth)
        return f"{self.model}:depth-{depth}"


class OfflineJudgment(BaseModel):
    score: Annotated[float, Field(ge=0, le=1)]
    acceptable: bool


class RedactedResponse(BaseModel):
    strategy: str
    destination: str | None
    confidence: float | None
    latency: timedelta
    score: float | None
    acceptable: bool
    moved: bool
    safety_failure: bool = False
    error: str | None = None


class RedactedCase(BaseModel):
    document: str
    reference_destination: str
    best_strategy: str | None
    best_response: int | None
    responses: list[RedactedResponse]


class StrategySummary(BaseModel):
    strategy: str
    completed: int
    acceptable: int
    acceptable_rate: float
    safety_failures: int
    mean_score: float
    mean_latency: timedelta


class ThresholdSummary(BaseModel):
    threshold: float
    accepted_moves: int
    judge_rejected_moves: int


class ConfidenceSelection(BaseModel, frozen=True):
    threshold: float
    summaries: list[ThresholdSummary]


class EvaluatedPlacement(NamedTuple):
    destination: ProposalDestination
    confidence: float
    judgment: OfflineJudgment


class RedactedReport(BaseModel):
    schema_version: int = 1
    generated_at: str
    mode: Literal["read-only"] = "read-only"
    requested_cases: int
    completed_cases: int
    strategies: list[StrategySummary]
    confidence_thresholds: list[ThresholdSummary]
    selected_strategy: str | None
    selected_confidence_threshold: float
    errors: dict[str, int] = Field(default_factory=dict)
    cases: list[RedactedCase]


class PrivateTokenizer:
    """Create stable-within-run tokens without retaining the HMAC key."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    def token(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{kind}_{digest}"


def eligible_documents(items: list[InventoryItem]) -> list[InventoryItem]:
    """Use only private, owned Docs with one unambiguous parent."""
    return [
        item
        for item in items
        if item.mime_type == MIME_DOCUMENT
        and item.owned_by_me
        and not item.shared
        and not item.trashed
        and len(item.parent_ids) == 1
    ]


def private_document_description(item: InventoryItem, content: str) -> str:
    """Build the transient model input; callers must never persist this value."""
    normalized_content = " ".join(content.split())[:2000]
    metadata = [f"Title: {item.name}", f"Type: {item.mime_type}"]
    if item.modified_time is not None:
        metadata.append(f"Modified: {item.modified_time}")
    metadata.append(f"Content excerpt: {normalized_content}")
    return "\n".join(metadata)[:2000]


def offline_judge(
    reference: ProposalDestination,
    proposed: ProposalDestination,
) -> OfflineJudgment:
    """Prefer the observed durable folder, while accepting a meaningful broad ancestor."""
    if proposed.folder_id == reference.folder_id:
        return OfflineJudgment(score=1, acceptable=True)
    reference_path = tuple(part.casefold() for part in reference.folder_path)
    proposed_path = tuple(part.casefold() for part in proposed.folder_path)
    is_ancestor = (
        len(proposed_path) > 1
        and len(proposed_path) < len(reference_path)
        and reference_path[: len(proposed_path)] == proposed_path
    )
    if is_ancestor:
        distance = len(reference_path) - len(proposed_path)
        return OfflineJudgment(score=max(0.6, 0.85 - 0.1 * (distance - 1)), acceptable=True)
    return OfflineJudgment(score=0, acceptable=False)


def select_strategy(summaries: list[StrategySummary]) -> str | None:
    safe = [summary for summary in summaries if summary.completed and not summary.safety_failures]
    if not safe:
        return None
    lite = [summary for summary in safe if "flash-lite" in summary.strategy]
    flash = [summary for summary in safe if "flash-lite" not in summary.strategy]
    if lite and flash:
        best_lite = max(lite, key=lambda item: item.acceptable_rate)
        best_flash = max(flash, key=lambda item: item.acceptable_rate)
        model_pool = (
            flash if best_flash.acceptable_rate >= best_lite.acceptable_rate + 0.05 else lite
        )
    else:
        model_pool = lite or flash
    best_rate = max(summary.acceptable_rate for summary in model_pool)
    near_best = [summary for summary in model_pool if summary.acceptable_rate >= best_rate - 0.02]
    return min(near_best, key=_strategy_preference).strategy


def select_confidence_threshold(
    responses: list[RedactedResponse],
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> ConfidenceSelection:
    summaries: list[ThresholdSummary] = []
    for threshold in thresholds:
        accepted = [
            response
            for response in responses
            if response.moved
            and response.confidence is not None
            and response.confidence >= threshold
        ]
        summaries.append(
            ThresholdSummary(
                threshold=threshold,
                accepted_moves=len(accepted),
                judge_rejected_moves=sum(not response.acceptable for response in accepted),
            )
        )
    selected = next(
        (summary.threshold for summary in summaries if summary.judge_rejected_moves == 0),
        thresholds[-1],
    )
    return ConfidenceSelection(threshold=selected, summaries=summaries)


def _strategy_preference(summary: StrategySummary) -> tuple[int, int, str]:
    depth = summary.strategy.rsplit("depth-", maxsplit=1)[-1]
    depth_rank = 10_000 if depth == "unlimited" else int(depth)
    model_rank = 0 if "flash-lite" in summary.strategy else 1
    return depth_rank, model_rank, summary.strategy


async def evaluate(args: argparse.Namespace) -> RedactedReport:  # ruff: ignore[too-many-locals]
    organizer = await load_drive_organizer(args.user_email)
    snapshot = await asyncio.to_thread(organizer.inventory)
    if not snapshot.complete:
        msg = "Drive inventory is incomplete; refusing to evaluate a partial folder tree"
        raise RuntimeError(msg)
    drive = DriveSummary(id=DriveId(snapshot.root_id), name="My Drive", is_personal=True)
    tree = build_personal_tree(snapshot.items, snapshot.root_id)
    destinations = smart_create_destinations(drive, [tree])
    by_id = {destination.folder_id: destination for destination in destinations}
    documents = [
        item for item in eligible_documents(snapshot.items) if FileId(item.parent_ids[0]) in by_id
    ]
    random.Random(args.seed).shuffle(  # ruff: ignore[suspicious-non-cryptographic-random-usage]
        documents
    )
    documents = documents[: args.cases]
    strategies = [
        Strategy(model=model, candidate_depth=depth)
        for model in args.models
        for depth in args.depths
    ]
    generators = {
        strategy.key: VertexProposalGenerator(project=args.project, model=strategy.model)
        for strategy in strategies
    }
    tokenizer = PrivateTokenizer()
    model_limit = asyncio.Semaphore(MODEL_CONCURRENCY)
    errors: Counter[str] = Counter()
    cases: list[RedactedCase] = []

    for item in documents:
        content = await asyncio.to_thread(organizer.sample_document, item)
        normalized_content = " ".join(content.split())[:2000]
        if len(normalized_content) < MIN_DESCRIPTION_CHARS:
            errors["empty_document"] += 1
            continue
        description = private_document_description(item, normalized_content)
        request = AIMode(
            kind="ai",
            description=description,
            file_type="document",
            invoking_folder_id=FileId(snapshot.root_id),
            invoking_drive_id=DriveId(snapshot.root_id),
        )
        reference = by_id[FileId(item.parent_ids[0])]
        responses = list(
            await asyncio.gather(
                *(
                    _evaluate_with_limit(
                        model_limit,
                        strategy=strategy,
                        generator=generators[strategy.key],
                        request=request,
                        reference=reference,
                        candidates=smart_create_candidates(
                            request,
                            [drive],
                            [tree],
                            candidate_depth=strategy.candidate_depth,
                        ),
                        tokenizer=tokenizer,
                    )
                    for strategy in strategies
                )
            )
        )
        for response in responses:
            if response.error is not None:
                errors[response.error] += 1
        best_index = _best_response_index(responses)
        cases.append(
            RedactedCase(
                document=tokenizer.token("doc", item.id),
                reference_destination=tokenizer.token("folder", reference.folder_id),
                best_strategy=(responses[best_index].strategy if best_index is not None else None),
                best_response=best_index,
                responses=responses,
            )
        )
        print(f"Evaluated {len(cases)}/{len(documents)} documents")

    summaries = summarize_strategies(cases, strategies)
    selected_strategy = select_strategy(summaries)
    selected_responses = [
        response
        for case in cases
        for response in case.responses
        if response.strategy == selected_strategy
    ]
    confidence_selection = select_confidence_threshold(selected_responses)
    return RedactedReport(
        generated_at=datetime.now(UTC).isoformat(),
        requested_cases=args.cases,
        completed_cases=len(cases),
        strategies=summaries,
        confidence_thresholds=confidence_selection.summaries,
        selected_strategy=selected_strategy,
        selected_confidence_threshold=confidence_selection.threshold,
        errors=dict(errors),
        cases=cases,
    )


async def _evaluate_with_limit(
    limit: asyncio.Semaphore,
    *,
    strategy: Strategy,
    generator: VertexProposalGenerator,
    request: AIMode,
    reference: ProposalDestination,
    candidates: list[ProposalDestination],
    tokenizer: PrivateTokenizer,
) -> RedactedResponse:
    async with limit:
        return await _evaluate_response(
            strategy=strategy,
            generator=generator,
            request=request,
            reference=reference,
            candidates=candidates,
            tokenizer=tokenizer,
        )


async def _evaluate_response(
    *,
    strategy: Strategy,
    generator: VertexProposalGenerator,
    request: AIMode,
    reference: ProposalDestination,
    candidates: list[ProposalDestination],
    tokenizer: PrivateTokenizer,
) -> RedactedResponse:
    started = time.monotonic()
    try:
        evaluated = await _generate_and_judge(generator, request, reference, candidates)
    except (
        Exception
    ) as exc:  # Private model text and exception messages are intentionally discarded.
        error = _safe_error_name(exc)
        return RedactedResponse(
            strategy=strategy.key,
            destination=None,
            confidence=None,
            latency=_elapsed(started),
            score=None,
            acceptable=False,
            moved=False,
            safety_failure=error in {"cross_drive", "sensitive_destination"},
            error=error,
        )
    return RedactedResponse(
        strategy=strategy.key,
        destination=tokenizer.token("folder", evaluated.destination.folder_id),
        confidence=evaluated.confidence,
        latency=_elapsed(started),
        score=evaluated.judgment.score,
        acceptable=evaluated.judgment.acceptable,
        moved=evaluated.destination.folder_id != request.invoking_folder_id,
    )


async def _generate_and_judge(
    generator: VertexProposalGenerator,
    request: AIMode,
    reference: ProposalDestination,
    candidates: list[ProposalDestination],
) -> EvaluatedPlacement:
    async with asyncio.timeout(MODEL_TIMEOUT_SECONDS):
        generated = await generator.generate(request, candidates, [])
    validated = validate_generated_proposal(request, generated, candidates, [])
    return EvaluatedPlacement(
        destination=validated.destination,
        confidence=generated.confidence,
        judgment=offline_judge(reference, validated.destination),
    )


def _elapsed(started: float) -> timedelta:
    return timedelta(seconds=time.monotonic() - started)


def _safe_error_name(exc: Exception) -> str:
    message = str(exc)
    if "another workspace" in message:
        return "cross_drive"
    if "sensitive destination" in message:
        return "sensitive_destination"
    if "unknown destination" in message:
        return "unknown_destination"
    return type(exc).__name__


def _best_response_index(responses: list[RedactedResponse]) -> int | None:
    eligible = [
        (index, response)
        for index, response in enumerate(responses)
        if response.score is not None and not response.safety_failure
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (item[1].score or 0, item[1].confidence or 0))[0]


def summarize_strategies(
    cases: list[RedactedCase], strategies: list[Strategy]
) -> list[StrategySummary]:
    summaries: list[StrategySummary] = []
    for strategy in strategies:
        responses = [
            response
            for case in cases
            for response in case.responses
            if response.strategy == strategy.key
        ]
        completed = [response for response in responses if response.score is not None]
        summaries.append(
            StrategySummary(
                strategy=strategy.key,
                completed=len(completed),
                acceptable=sum(response.acceptable for response in completed),
                acceptable_rate=(
                    sum(response.acceptable for response in completed) / len(completed)
                    if completed
                    else 0
                ),
                safety_failures=sum(response.safety_failure for response in responses),
                mean_score=(
                    sum(response.score or 0 for response in completed) / len(completed)
                    if completed
                    else 0
                ),
                mean_latency=(
                    sum((response.latency for response in completed), start=timedelta())
                    / len(completed)
                    if completed
                    else timedelta()
                ),
            )
        )
    return summaries


def write_private_report(path: Path, report: RedactedReport) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def parse_depth(value: str) -> int | None:
    if value == "unlimited":
        return None
    depth = int(value)
    if depth < 0:
        msg = "candidate depths must be non-negative"
        raise argparse.ArgumentTypeError(msg)
    return depth


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--cases", type=int, default=DEFAULT_CASES)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument("--depths", nargs="+", type=parse_depth, default=list(DEFAULT_DEPTHS))
    parser.add_argument("--project", default=settings.GOOGLE_CLOUD_PROJECT)
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.cases < 1:
        parser.error("--cases must be positive")
    if not args.project:
        parser.error("--project is required when GOOGLE_CLOUD_PROJECT is not configured")
    return args


async def main() -> None:
    args = parse_args()
    report = await evaluate(args)
    write_private_report(args.output, report)
    print(
        f"Completed {report.completed_cases}/{report.requested_cases}; "
        f"strategy={report.selected_strategy or 'none'}; "
        f"confidence_threshold={report.selected_confidence_threshold:.2f}"
    )
    print(f"Redacted report: {args.output}")
    if report.completed_cases == 0:
        msg = "Evaluation failed: no private document produced an evaluation case"
        raise SystemExit(msg)


if __name__ == "__main__":
    asyncio.run(main())
