from copy import deepcopy
from dataclasses import dataclass, replace

from loguru import logger
from pydantic import JsonValue

from app.document_transform.brand import mutation_sha256
from app.document_transform.errors import ModelUnavailableError, SourceTooLargeError
from app.document_transform.models import PatchGenerator
from app.document_transform.readability_json import bodies, content, document_digest, obj
from app.document_transform.readability_lists import validate_readable_lists, verify_readable_lists
from app.document_transform.readability_paragraphs import (
    project_readable_paragraphs,
    readability_document,
    readable_paragraph_requests,
    validated_readability_paragraphs,
)
from app.document_transform.readability_tables import (
    plan_readable_tables,
    project_readable_tables,
    readable_table_requests,
)
from app.document_transform.readability_types import (
    CURRENT_READABILITY_VERSION,
    READABILITY_PARAGRAPH_WORD_LIMIT,
    PlannedReadabilityCheckpoint,
    ReadabilityCheckpoint,
    ReadabilityParagraph,
    ReadabilityPart,
    ReadabilityProposal,
    ReadabilityStopReason,
    ReadabilityTable,
)
from app.identifiers import DocsRevisionId
from app.performance import provider_retry_remaining_seconds


MAX_READABILITY_PASSES = 100
# Leave at least 60 seconds for a layout call and the model adapter's existing
# 30-second write/readback reserve. A 180-second admission floor skipped layout
# after an ordinary one-minute editorial pass in the four-minute worker budget.
_MIN_LAYOUT_SECONDS = 90
_MAX_LAYOUT_CHARACTERS = 60000
# A useful group of 2-6 facts should improve on one short paragraph. Tiny
# fragments still incur a separate penalty in the layout score.
_LIST_ITEM_BURDEN = 8
# A short introduction or qualification belongs to its adjacent list group.
# Charging a full paragraph would reject useful lists for retaining that context.
_LIST_CONTEXT_BURDEN = 4
_KNOWN_PROJECTION_FAILURES = frozenset(
    {
        "Paragraph identity changed",
        "Paragraph source changed",
        "List formatting was not applied",
        "Paragraph text or position changed",
        "Paragraph formatting was not applied or unplanned properties changed",
        "Unplanned inline formatting changed",
        "Existing list definitions changed",
        "A generated list changed unrelated content",
    }
)


@dataclass(frozen=True, slots=True)
class _LayoutDraft:
    proposals: list[ReadabilityProposal]
    paragraphs: tuple[ReadabilityParagraph, ...]
    passes: int
    planning_failure_code: str | None = None
    accepted_improvements: int = 0
    stop_reason: ReadabilityStopReason = "not_eligible"


@dataclass(frozen=True, slots=True)
class _ReadabilityMutations:
    paragraphs: tuple[ReadabilityParagraph, ...]
    tables: tuple[ReadabilityTable, ...]
    requests: list[dict[str, JsonValue]]


async def plan_readability(
    document: dict[str, JsonValue],
    *,
    title: str,
    generator: PatchGenerator,
    max_passes: int = MAX_READABILITY_PASSES,
) -> ReadabilityCheckpoint | None:
    if not 1 <= max_passes <= MAX_READABILITY_PASSES:
        msg = "Readability passes must be between 1 and 100"
        raise ValueError(msg)
    draft = await _refine_layout(document, title=title, generator=generator, max_passes=max_passes)
    mutations = _bounded_readability_mutations(document, draft.paragraphs)
    if not mutations.requests and not draft.passes and draft.planning_failure_code is None:
        return None
    checkpoint = PlannedReadabilityCheckpoint(
        version=CURRENT_READABILITY_VERSION,
        source_revision_id=DocsRevisionId(str(document["revisionId"])),
        source_sha256=document_digest(document),
        invariant_sha256="",
        paragraphs=mutations.paragraphs,
        tables=mutations.tables,
        original_lists={
            str(tab_id): deepcopy(obj(tab.get("lists", {}))) for tab_id, tab in bodies(document)
        },
        requests=mutations.requests,
        model_invoked=bool(draft.passes),
        passes=draft.passes,
        accepted_improvements=draft.accepted_improvements,
        stop_reason=draft.stop_reason,
        planning_failure_code=draft.planning_failure_code,
    )
    checkpoint = checkpoint.model_copy(
        update={
            "invariant_sha256": readability_invariant_sha256(document, checkpoint, applied=False)
        }
    )
    if not verify_readability(document, checkpoint):
        return checkpoint
    if not draft.passes and draft.planning_failure_code is None:
        return None
    # Retain the attempted model outcome even when no native edits are needed.
    # A pending zero-request plan is durably saved and strictly read back by the
    # ordinary provider path, which performs no write for an already exact match.
    unchanged = checkpoint.model_copy(update={"paragraphs": (), "tables": (), "requests": []})
    return unchanged.model_copy(
        update={
            "invariant_sha256": readability_invariant_sha256(document, unchanged, applied=False)
        }
    )


def _bounded_readability_mutations(
    document: dict[str, JsonValue], paragraphs: tuple[ReadabilityParagraph, ...]
) -> _ReadabilityMutations:
    tables = plan_readable_tables(document)
    while paragraphs or tables:
        try:
            validate_readable_lists(document, paragraphs)
        except ValueError:
            paragraphs = paragraphs[:-1]
            continue
        requests = readable_paragraph_requests(paragraphs) + readable_table_requests(
            document, tables
        )
        try:
            mutation_sha256(requests)
        except SourceTooLargeError:
            if tables:
                tables = tables[:-1]
            else:
                paragraphs = paragraphs[:-1]
        else:
            return _ReadabilityMutations(paragraphs, tables, requests)
    return _ReadabilityMutations((), (), [])


async def _refine_layout(
    document: dict[str, JsonValue], *, title: str, generator: PatchGenerator, max_passes: int
) -> _LayoutDraft:
    ir = readability_document(document, title=title)
    best = _LayoutDraft([], (), 0)
    if (
        not generator.supports_readability_planning
        or not any(block.editable for block in ir.blocks)
        or sum(len(block.text) for block in ir.blocks) > _MAX_LAYOUT_CHARACTERS
    ):
        return best
    score = _layout_burden([], document, title=title)
    feedback = ""
    best = replace(best, stop_reason="pass_limit")
    for number in range(1, max_passes + 1):
        remaining = provider_retry_remaining_seconds()
        if remaining is not None and remaining < _MIN_LAYOUT_SECONDS:
            best = replace(
                best,
                planning_failure_code="model_deadline_exhausted",
                stop_reason="deadline_reserve",
            )
            break
        # Failed provider calls still count as attempts in table-only receipts.
        best = replace(best, passes=number)
        try:
            proposals = await generator.plan_readability(
                ir, previous=best.proposals, feedback=feedback
            )
            paragraphs = validated_readability_paragraphs(document, ir, proposals)
        except ModelUnavailableError as error:
            best = _provider_stopped_draft(best, error)
            break
        except ValueError as error:
            if feedback:
                best = replace(
                    best,
                    planning_failure_code="model_output_invalid",
                    stop_reason="invalid_proposal",
                )
                break
            feedback = str(error)
            continue
        candidate_score = _layout_burden(proposals, document, title=title)
        if candidate_score >= score:
            best = replace(
                best,
                stop_reason="converged" if proposals == best.proposals else "no_improvement",
            )
            break
        best, score = (
            replace(
                best,
                proposals=proposals,
                paragraphs=paragraphs,
                accepted_improvements=best.accepted_improvements + 1,
            ),
            candidate_score,
        )
        feedback = "Further improve the previous layout only if it makes the whole document easier to read. Keep text exact."
    return best


def _provider_stopped_draft(best: _LayoutDraft, error: ModelUnavailableError) -> _LayoutDraft:
    reason: ReadabilityStopReason = "provider_unavailable"
    if error.code == "model_deadline_exhausted":
        reason = "deadline_reserve"
    return replace(best, planning_failure_code=error.code, stop_reason=reason)


def _layout_burden(
    proposals: list[ReadabilityProposal], document: dict[str, JsonValue], *, title: str
) -> int:
    by_id = {proposal.block_id: proposal for proposal in proposals}
    score = 0
    for block in readability_document(document, title=title).blocks:
        if not block.editable:
            continue
        proposal = by_id.get(block.id)
        if proposal is None:
            score += max(0, len(block.text.split()) - READABILITY_PARAGRAPH_WORD_LIMIT) ** 2 + 60
            continue
        score += sum(
            _part_layout_burden(proposal.parts, index) for index in range(len(proposal.parts))
        )
    return score


def _part_layout_burden(parts: tuple[ReadabilityPart, ...], index: int) -> int:
    part = parts[index]
    words = len(part.text.split())
    fragments = 0
    if part.role == "paragraph":
        overhead = 60
        if words <= READABILITY_PARAGRAPH_WORD_LIMIT and _adjacent_to_list(parts, index):
            overhead = _LIST_CONTEXT_BURDEN
    elif part.role in {"bullet", "numbered"}:
        overhead = _LIST_ITEM_BURDEN
        fragments = max(0, 2 - words) * 50
    else:
        overhead = 25
    return max(0, words - READABILITY_PARAGRAPH_WORD_LIMIT) ** 2 + overhead + fragments


def _adjacent_to_list(parts: tuple[ReadabilityPart, ...], index: int) -> bool:
    return any(
        parts[neighbor].role in {"bullet", "numbered"}
        for neighbor in (index - 1, index + 1)
        if 0 <= neighbor < len(parts)
    )


def verify_readability(
    document: dict[str, JsonValue], checkpoint: ReadabilityCheckpoint, *, diagnostics: bool = False
) -> bool:
    try:
        observed = readability_invariant_sha256(document, checkpoint, applied=True)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        # Only fixed, code-owned messages may cross the diagnostic boundary.
        # KeyError values and provider/document values are never emitted.
        if diagnostics:
            logger.warning(
                "document readability native projection rejected",
                invariant=str(error)
                if type(error) is ValueError and str(error) in _KNOWN_PROJECTION_FAILURES
                else "protected_native_projection",
                exception_type=type(error).__name__,
            )
        return False
    if diagnostics and observed != checkpoint.invariant_sha256:
        logger.warning(
            "document readability native invariant mismatch",
            invariant="unplanned_native_difference",
            expected_sha256=checkpoint.invariant_sha256,
            observed_sha256=observed,
        )
    return observed == checkpoint.invariant_sha256


def readability_invariant_sha256(
    document: dict[str, JsonValue], checkpoint: ReadabilityCheckpoint, *, applied: bool
) -> str:
    return document_digest(_project(document, checkpoint, applied=applied))


def _project(
    document: dict[str, JsonValue], checkpoint: ReadabilityCheckpoint, *, applied: bool
) -> dict[str, JsonValue]:
    projected = deepcopy(document)
    if applied:
        verify_readable_lists(
            projected, checkpoint.paragraphs, original_lists=checkpoint.original_lists
        )
    project_readable_tables(
        projected, checkpoint.tables, applied=applied, version=checkpoint.version
    )
    project_readable_paragraphs(projected, checkpoint.paragraphs, applied=applied)
    for tab_id, tab in bodies(projected):
        original = checkpoint.original_lists[str(tab_id)]
        lists = obj(tab.get("lists", {}))
        if applied:
            remaining = _referenced_lists(content(tab))
            for identifier in list(lists):
                if identifier not in original:
                    if identifier in remaining:
                        msg = "A generated list changed unrelated content"
                        raise ValueError(msg)
                    lists.pop(identifier)
        if lists != original:
            msg = "Existing list definitions changed"
            raise ValueError(msg)
        if not lists:
            tab.pop("lists", None)
    return projected


def _referenced_lists(value: JsonValue) -> set[str]:
    match value:
        case list():
            return set().union(*(_referenced_lists(child) for child in value))
        case dict():
            result = {str(value["listId"])} if "listId" in value else set()
            return result.union(*(_referenced_lists(child) for child in value.values()))
        case _:
            return set()
