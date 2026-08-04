"""Stress-test Re-organize on My Drive without mutating or logging private data."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime
import hashlib
import hmac
import json
from pathlib import Path
import random
import secrets
from typing import TYPE_CHECKING, Annotated

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field

from app.config import settings
from app.drive.schemas import MIME_FOLDER, DriveNode, DriveSummary, DriveTree
from app.identifiers import DriveId, FileId
from app.smart_create.service import (
    GeneratedReorganization,
    ReorganizationEvidence,
    VertexReorganizationGenerator,
    organization_candidates,
)
from scripts.organize_my_drive import MIME_DOCUMENT, InventoryItem, load_drive_organizer


if TYPE_CHECKING:
    from app.smart_create.schemas import ProposalDestination


DEFAULT_CASES = 100
DEFAULT_RESPONSES_PER_CASE = 5
DEFAULT_JUDGE_MODEL = "gemini-3.5-flash"
DEFAULT_OUTPUT = Path(".drive-organizer/reorganize-eval.json")
MODEL_TIMEOUT_SECONDS = 30
JUDGE_ATTEMPTS = 3


class ReferenceJudgment(BaseModel):
    destination_folder_id: FileId
    confidence: Annotated[float, Field(ge=0, le=1)]
    reason: str


class ResponseJudgment(BaseModel):
    best_response_index: Annotated[int, Field(ge=0)]
    score: Annotated[float, Field(ge=0, le=1)]
    acceptable: bool
    reason: str


class RedactedCase(BaseModel):
    document: str
    current_destination: str
    reference_destination: str
    best_destination: str | None
    best_response_index: int | None
    score: float | None
    acceptable: bool
    candidate_responses: int
    errors: list[str]


class RedactedReport(BaseModel):
    schema_version: int = 2
    generated_at: str
    mode: str = "read-only"
    candidate_model: str
    judge_model: str
    requested_cases: int
    responses_per_case: int
    candidate_concurrency: int
    completed_cases: int
    acceptable_cases: int
    evaluation_errors: dict[str, int] = Field(default_factory=dict)
    cases: list[RedactedCase]


class PrivateTokenizer:
    """Create stable-within-run tokens without retaining the HMAC key."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    def token(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:16]
        return f"{kind}_{digest}"


class ReorganizationJudge:
    def __init__(self, model: str, project: str) -> None:
        self._model = model
        self.error_counts: Counter[str] = Counter()
        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=settings.GEMINI_LOCATION,
        )

    async def reference(
        self,
        *,
        evidence: ReorganizationEvidence,
        current: ProposalDestination,
        folders: list[ProposalDestination],
    ) -> ReferenceJudgment:
        context = {
            "current_destination": current.model_dump(mode="json"),
            "document": evidence.model_dump(mode="json"),
            "folders": [folder.model_dump(mode="json") for folder in folders],
        }
        instructions = (
            "Independently choose the best folder for this document. Use content, title, "
            "description, modified time, current location, and complete folder paths. Do not "
            "infer sensitive categories from personal names or weak keyword matches. Prefer the "
            "current folder when evidence is unclear. Select only a supplied folder ID."
        )
        serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        prompt = f"{instructions}\n{serialized}"
        return await self._generate(prompt, ReferenceJudgment)

    async def rank(
        self,
        *,
        evidence: ReorganizationEvidence,
        current: ProposalDestination,
        reference: ReferenceJudgment,
        responses: list[GeneratedReorganization],
        folders: list[ProposalDestination],
    ) -> ResponseJudgment:
        context = {
            "current_destination": current.model_dump(mode="json"),
            "document": evidence.model_dump(mode="json"),
            "folders": [folder.model_dump(mode="json") for folder in folders],
            "independent_reference": reference.model_dump(mode="json"),
            "responses": [response.model_dump(mode="json") for response in responses],
        }
        instructions = (
            "Rank the responses for semantic fit and safety. Prefer a response grounded in the "
            "document over a superficial title or name match. A response may beat the independent "
            "reference if its reasoning is stronger. Mark acceptable only when the destination is "
            "clearly supported. best_response_index is zero-based."
        )
        serialized = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        prompt = f"{instructions}\n{serialized}"
        judgment = await self._generate(prompt, ResponseJudgment)
        if judgment.best_response_index >= len(responses):
            msg = "Judge selected a response index outside the supplied list"
            raise ValueError(msg)
        return judgment

    async def _generate[Result: BaseModel](self, prompt: str, schema: type[Result]) -> Result:
        for attempt in range(JUDGE_ATTEMPTS):
            try:
                return await self._generate_once(prompt, schema)
            except Exception as exc:
                self.error_counts[type(exc).__name__] += 1
                if attempt == JUDGE_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(2**attempt)
        msg = "Judge retry loop ended unexpectedly"
        raise RuntimeError(msg)

    async def _generate_once[Result: BaseModel](self, prompt: str, schema: type[Result]) -> Result:
        async with asyncio.timeout(MODEL_TIMEOUT_SECONDS):
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    thinking_config=genai_types.ThinkingConfig(
                        thinking_level=genai_types.ThinkingLevel.LOW
                    ),
                    http_options=genai_types.HttpOptions(timeout=MODEL_TIMEOUT_SECONDS * 1000),
                ),
            )
        if isinstance(response.parsed, schema):
            return response.parsed
        if not response.text:
            msg = "Judge returned an empty response"
            raise ValueError(msg)
        return schema.model_validate_json(response.text)


def build_personal_tree(items: list[InventoryItem], root_id: str) -> DriveTree:
    folders = [
        DriveNode(
            id=FileId(item.id),
            name=item.name,
            mime_type=MIME_FOLDER,
            parent_id=_tree_parent(item, root_id),
            modified_time=item.modified_time,
        )
        for item in items
        if item.is_folder
    ]
    return DriveTree(
        drive_id=DriveId(root_id),
        folders=folders,
        files=[],
        files_complete=True,
    )


def eligible_documents(items: list[InventoryItem]) -> list[InventoryItem]:
    return [
        item
        for item in items
        if item.mime_type == MIME_DOCUMENT
        and item.owned_by_me
        and not item.trashed
        and len(item.parent_ids) <= 1
    ]


def make_redacted_case(
    *,
    tokenizer: PrivateTokenizer,
    item: InventoryItem,
    current: ProposalDestination,
    reference: ReferenceJudgment,
    responses: list[GeneratedReorganization],
    judgment: ResponseJudgment | None,
    errors: list[str],
) -> RedactedCase:
    best = responses[judgment.best_response_index] if judgment is not None else None
    return RedactedCase(
        document=tokenizer.token("doc", item.id),
        current_destination=tokenizer.token("folder", current.folder_id),
        reference_destination=tokenizer.token("folder", reference.destination_folder_id),
        best_destination=(
            tokenizer.token("folder", best.destination_folder_id) if best is not None else None
        ),
        best_response_index=judgment.best_response_index if judgment is not None else None,
        score=judgment.score if judgment is not None else None,
        acceptable=judgment.acceptable if judgment is not None else False,
        candidate_responses=len(responses),
        errors=errors,
    )


async def evaluate(args: argparse.Namespace) -> RedactedReport:  # ruff: ignore[too-many-locals]
    organizer = await load_drive_organizer(args.user_email)
    snapshot = await asyncio.to_thread(organizer.inventory)
    if not snapshot.complete:
        msg = "Drive inventory is incomplete; refusing to evaluate a partial folder tree"
        raise RuntimeError(msg)
    tree = build_personal_tree(snapshot.items, snapshot.root_id)
    drive = DriveSummary(id=DriveId(snapshot.root_id), name="My Drive", is_personal=True)
    documents = eligible_documents(snapshot.items)
    random.Random(args.seed).shuffle(  # ruff: ignore[suspicious-non-cryptographic-random-usage] - deterministic sampling is intended.
        documents
    )
    documents = documents[: args.cases]
    judge = ReorganizationJudge(args.judge_model, args.project)
    generator = VertexReorganizationGenerator(project=args.project, model=args.candidate_model)
    candidate_semaphore = asyncio.Semaphore(args.candidate_concurrency)
    tokenizer = PrivateTokenizer()
    cases: list[RedactedCase] = []

    for item in documents:
        errors: list[str] = []
        content = await asyncio.to_thread(organizer.sample_document, item)
        if not content.strip():
            continue
        evidence = ReorganizationEvidence(
            content=content,
            description=None,
            file_name=item.name,
            modified_time=item.modified_time,
        )
        current_parent = FileId(item.parent_ids[0]) if item.parent_ids else None
        folders = organization_candidates(evidence, current_parent, drive, tree)
        current_id = current_parent or FileId(snapshot.root_id)
        current = next(folder for folder in folders if folder.folder_id == current_id)
        try:
            reference = await judge.reference(
                evidence=evidence,
                current=current,
                folders=folders,
            )
        except Exception as exc:  # The report records only the exception class, never its text.
            error_name = type(exc).__name__
            print(f"Reference judge failed: {error_name}")
            continue
        if not any(folder.folder_id == reference.destination_folder_id for folder in folders):
            print("Reference judge returned an unknown destination")
            continue
        generated_responses = await asyncio.gather(
            *(
                _generate_candidate_response(
                    generator,
                    current,
                    evidence,
                    folders,
                    candidate_semaphore,
                )
                for _ in range(args.responses_per_case)
            )
        )
        responses: list[GeneratedReorganization] = []
        for response, error in generated_responses:
            if error is not None:
                errors.append(error)
            elif response is not None and any(
                folder.folder_id == response.destination_folder_id for folder in folders
            ):
                responses.append(response)
            else:
                errors.append("unknown_destination")
        judgment: ResponseJudgment | None = None
        if responses:
            try:
                judgment = await judge.rank(
                    evidence=evidence,
                    current=current,
                    reference=reference,
                    responses=responses,
                    folders=folders,
                )
            except Exception as exc:
                errors.append(type(exc).__name__)
        cases.append(
            make_redacted_case(
                tokenizer=tokenizer,
                item=item,
                current=current,
                reference=reference,
                responses=responses,
                judgment=judgment,
                errors=errors,
            )
        )
        print(f"Evaluated {len(cases)}/{len(documents)} documents")

    return RedactedReport(
        generated_at=datetime.now(UTC).isoformat(),
        candidate_model=args.candidate_model,
        judge_model=args.judge_model,
        requested_cases=args.cases,
        responses_per_case=args.responses_per_case,
        candidate_concurrency=args.candidate_concurrency,
        completed_cases=len(cases),
        acceptable_cases=sum(case.acceptable for case in cases),
        evaluation_errors=dict(judge.error_counts),
        cases=cases,
    )


def write_private_report(path: Path, report: RedactedReport) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


async def _generate_candidate_response(
    generator: VertexReorganizationGenerator,
    current: ProposalDestination,
    evidence: ReorganizationEvidence,
    folders: list[ProposalDestination],
    semaphore: asyncio.Semaphore,
) -> tuple[GeneratedReorganization | None, str | None]:
    try:
        async with semaphore:
            response = await generator.generate(
                current=current,
                evidence=evidence,
                folders=folders,
            )
    except Exception as exc:  # No model text or private context reaches disk.
        return None, type(exc).__name__
    return response, None


def summarize(report: RedactedReport) -> str:
    scores = [score for case in report.cases if (score := case.score) is not None]
    mean_score = sum(scores) / len(scores) if scores else 0.0
    error_counts = Counter(error for case in report.cases for error in case.errors)
    error_counts.update(report.evaluation_errors)
    errors = ", ".join(f"{name}={count}" for name, count in sorted(error_counts.items()))
    return (
        f"Completed {report.completed_cases}/{report.requested_cases}; "
        f"acceptable={report.acceptable_cases}; mean_score={mean_score:.3f}; "
        f"errors={errors or 'none'}"
    )


def _tree_parent(item: InventoryItem, root_id: str) -> FileId | None:
    if not item.parent_ids or item.parent_ids[0] == root_id:
        return None
    return FileId(item.parent_ids[0])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--cases", type=int, default=DEFAULT_CASES)
    parser.add_argument("--responses-per-case", type=int, default=DEFAULT_RESPONSES_PER_CASE)
    parser.add_argument("--candidate-concurrency", type=int, default=DEFAULT_RESPONSES_PER_CASE)
    parser.add_argument("--project", default=settings.GOOGLE_CLOUD_PROJECT)
    parser.add_argument("--candidate-model", default=settings.GEMINI_REORGANIZATION_MODEL)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.cases < 1 or args.responses_per_case < 1 or args.candidate_concurrency < 1:
        parser.error("--cases, --responses-per-case, and --candidate-concurrency must be positive")
    if not args.project:
        parser.error("--project is required when GOOGLE_CLOUD_PROJECT is not configured")
    return args


async def main() -> None:
    args = parse_args()
    report = await evaluate(args)
    write_private_report(args.output, report)
    print(summarize(report))
    print(f"Redacted report: {args.output}")
    if report.completed_cases == 0:
        msg = "Evaluation failed: no document received a complete judge result"
        raise SystemExit(msg)


if __name__ == "__main__":
    asyncio.run(main())
