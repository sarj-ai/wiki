from typing import Literal, override

import pytest

from app.document_transform.ir import DocumentIR
from app.document_transform.readability import plan_readability
from app.document_transform.readability_types import ReadabilityPart, ReadabilityProposal
from tests.test_document_readability import _LayoutEditor
from tests.test_readability_lists import _prose_document


class _ParallelFactsEditor(_LayoutEditor):
    def __init__(self, parts: tuple[ReadabilityPart, ...]) -> None:
        super().__init__()
        self.parts = parts

    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        self.calls += 1
        if previous:
            return previous
        block = document.blocks[0]
        return [
            ReadabilityProposal(block_id=block.id, source_sha256=block.sha256, parts=self.parts)
        ]


@pytest.mark.parametrize("count", range(2, 7))
@pytest.mark.parametrize("role", ["bullet", "numbered"])
async def test_short_parallel_facts_form_native_lists_and_converge(
    count: int, role: Literal["bullet", "numbered"]
) -> None:
    procedure = (
        "First measure the baseline.",
        "Then change one variable.",
        "Finally record the uncertainty.",
        "Keep the original configuration.",
        "Retain the exact measurement.",
        "Record the operating conditions.",
    )
    parallel = (
        "Cache reads take 1 µs.",
        "Memory reads take 100 ns.",
        "Disk reads take 10 ms.",
        "Queue depth stays below 20.",
        "Requests use at most 4 retries.",
        "Samples retain 12.3 ± 0.4 ms.",
    )
    facts = (parallel if role == "bullet" else procedure)[:count]
    document = _prose_document((" ".join(facts),))
    editor = _ParallelFactsEditor(tuple(ReadabilityPart(text=text, role=role) for text in facts))

    plan = await plan_readability(document, title="Measurement procedure", generator=editor)

    assert plan is not None
    assert plan.passes == editor.calls == 2
    assert len(plan.paragraphs) == 1
    assert tuple(part.text for part in plan.paragraphs[0].parts) == facts
    assert sum("createParagraphBullets" in request for request in plan.requests) == 1


async def test_one_word_fragments_do_not_beat_a_readable_paragraph() -> None:
    facts = ("First", "measure", "then", "record")
    document = _prose_document((" ".join(facts),))
    editor = _ParallelFactsEditor(
        tuple(ReadabilityPart(text=text, role="bullet") for text in facts)
    )

    assert await plan_readability(document, title="Fragments", generator=editor) is None
    assert editor.calls == 1


@pytest.mark.parametrize("count", [2, 3, 6])
@pytest.mark.parametrize("role", ["bullet", "numbered"])
@pytest.mark.parametrize("context_position", ["before", "after", "both"])
async def test_short_lists_keep_their_qualifying_context(
    count: int, role: Literal["bullet", "numbered"], context_position: str
) -> None:
    facts = (
        (
            "Cache reads take 1 µs.",
            "Memory reads take 100 ns.",
            "Disk reads take 10 ms.",
            "Queue depth stays below 20.",
            "Requests use at most 4 retries.",
            "Samples retain 12.3 ± 0.4 ms.",
        )
        if role == "bullet"
        else (
            "First measure the baseline.",
            "Then change one variable.",
            "Finally record the uncertainty.",
            "Keep the original configuration.",
            "Retain the exact measurement.",
            "Record the operating conditions.",
        )
    )[:count]
    parts = tuple(ReadabilityPart(text=text, role=role) for text in facts)
    if context_position in {"before", "both"}:
        parts = (ReadabilityPart(text="These figures are estimates.", role="paragraph"), *parts)
    if context_position in {"after", "both"}:
        parts = (
            *parts,
            ReadabilityPart(text="They are not production measurements.", role="paragraph"),
        )
    source = " ".join(part.text for part in parts)
    editor = _ParallelFactsEditor(parts)

    plan = await plan_readability(
        _prose_document((source,)), title="Qualified facts", generator=editor
    )

    assert plan is not None
    assert plan.paragraphs[0].parts == parts
    assert " ".join(part.text for part in plan.paragraphs[0].parts) == source
    assert plan.passes == editor.calls == 2
    assert sum("createParagraphBullets" in request for request in plan.requests) == 1
