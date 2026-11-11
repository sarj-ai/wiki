from copy import deepcopy
from dataclasses import dataclass

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
    PlannedReadabilityCheckpoint,
    ReadabilityCheckpoint,
    ReadabilityParagraph,
    ReadabilityProposal,
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


@dataclass(frozen=True, slots=True)
class _LayoutDraft:
    proposals: list[ReadabilityProposal]
    paragraphs: tuple[ReadabilityParagraph, ...]
    passes: int


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
    if not mutations.requests:
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
    )
    checkpoint = checkpoint.model_copy(
        update={
            "invariant_sha256": readability_invariant_sha256(document, checkpoint, applied=False)
        }
    )
    return None if verify_readability(document, checkpoint) else checkpoint


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
    for number in range(1, max_passes + 1):
        remaining = provider_retry_remaining_seconds()
        if remaining is not None and remaining < _MIN_LAYOUT_SECONDS:
            break
        # Failed provider calls still count as attempts in table-only receipts.
        best = _LayoutDraft(best.proposals, best.paragraphs, number)
        try:
            proposals = await generator.plan_readability(
                ir, previous=best.proposals, feedback=feedback
            )
            paragraphs = validated_readability_paragraphs(document, ir, proposals)
        except ModelUnavailableError:
            break
        except ValueError as error:
            if feedback:
                break
            feedback = str(error)
            continue
        candidate_score = _layout_burden(proposals, document, title=title)
        if candidate_score >= score:
            break
        best, score = _LayoutDraft(proposals, paragraphs, number), candidate_score
        feedback = "Further improve the previous layout only if it makes the whole document easier to read. Keep text exact."
    return best


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
            score += max(0, len(block.text.split()) - 45) ** 2 + 60
            continue
        for part in proposal.parts:
            words = len(part.text.split())
            score += max(0, words - 45) ** 2 + (
                60
                if part.role == "paragraph"
                else _LIST_ITEM_BURDEN
                if part.role in {"bullet", "numbered"}
                else 25
            )
            score += max(0, 2 - words) * 50 if part.role in {"bullet", "numbered"} else 0
    return score


def verify_readability(document: dict[str, JsonValue], checkpoint: ReadabilityCheckpoint) -> bool:
    try:
        return (
            readability_invariant_sha256(document, checkpoint, applied=True)
            == checkpoint.invariant_sha256
        )
    except KeyError, IndexError, TypeError, ValueError:
        return False


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
