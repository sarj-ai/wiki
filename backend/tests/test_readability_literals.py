from copy import deepcopy
import string
from typing import override

from pydantic import JsonValue
import pytest

from app.document_transform.errors import OutputConflictError
from app.document_transform.ir import DocumentIR
from app.document_transform.readability import plan_readability, verify_readability
from app.document_transform.readability_json import array, document_digest, obj
from app.document_transform.readability_paragraphs import (
    project_readable_paragraphs,
    readability_document,
    readable_paragraph_requests,
    validated_readability_paragraphs,
)
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_tables import (
    project_readable_tables,
    readable_table_requests,
)
from app.document_transform.readability_types import (
    PlannedReadabilityCheckpoint,
    ReadabilityCheckpoint,
    ReadabilityParagraph,
    ReadabilityPart,
    ReadabilityProposal,
)
from app.identifiers import DocsRevisionId, FileId
from tests.test_document_readability import _fixture, _LayoutEditor, _paragraph, _plan
from tests.test_readability_lists import _prose_document


@pytest.mark.parametrize(
    "marked",
    [
        'The report states "measure latency|then select storage" before release.',
        "The report states “measure latency|then select storage” before release.",
        "The report states 'measure latency|then select storage' before release.",
        "The report states ‘measure latency|then select storage’ before release.",
        "The report states 'we don't have|enough data' before release.",
        "The report states ‘we don’t have|enough data’ before release.",
        "The report states 'the team's samples|contain enough data' before release.",
        "The report states ‘the team’s samples|contain enough data’ before release.",
        "Keep the exact literal `query =|LIMIT 10` before release.",
        "Keep the exact literal ```SELECT|LIMIT 10``` before release.",
        "Measurements remain approximately 12.3|± 0.4 ms under load.",
        "Measurements remain approximately 12.3 ±|0.4 ms under load.",
        "Measurements remain approximately 12.3 ± 0.4|ms under load.",
        "Measurements remain approximately 1.20e-6|s under load.",
        "Measurements remain approximately 1.2 ×|10^-6 s under load.",
        "The recipe requires 1 /|4 kg of flour.",
        "The budget is $|1,250.00 per month.",
        "Keep the citation [1,|2] before release.",
        "Keep the citation [Google|SRE](https://sre.google/books/) before release.",
        "Measurements remain approximately 12.3|m/s² under load.",
        "The accounting amount is ($|1,250.00) per month.",
        "Measurements remain approximately 1|234.50 ms under load.",
        "Measurements remain approximately 1 234|567.80 ms under load.",
        "The exposure remains approximately 1|1/2 h under load.",
        "The exposure remains approximately 1|½ h under load.",
        "Measurements remain approximately 1.234(5)|ms under load.",
        "Measurements remain approximately 1.234(5)|× 10^-6 s under load.",
        "Measurements remain approximately 9.81 m|/ s² under load.",
        "Measurements remain approximately 9.81 m /|s² under load.",
        "Measurements remain approximately 3 kg|· m / s² under load.",
        "Measurements remain approximately 3 kg ·|m / s² under load.",
        "Measurements remain approximately 3 m|^ 2 under load.",
        "Measurements remain approximately 3 m ^|2 under load.",
        "Measurements remain approximately 3 m|** 2 under load.",
        "Measurements remain approximately 3 m **|2 under load.",
        "Measurements remain approximately 9.81 m|s^-2 under load.",
        "Measurements remain approximately 3 kg m|s⁻² under load.",
        "Measurements remain approximately 12.3 ms|± 0.4 ms under load.",
        "Measurements remain approximately 12.3 ms ±|0.4 ms under load.",
        "Measurements remain approximately 1.2|x 10^3 s under load.",
        "Measurements remain approximately 1.2 x|10^3 s under load.",
    ],
)
def test_layout_rejects_paragraph_breaks_inside_source_literals(marked: str) -> None:
    document = _prose_document((marked.replace("|", " "),))
    ir = readability_document(document, title="Protected layout literals")
    block = ir.blocks[0]
    assert block.editable
    proposal = ReadabilityProposal(
        block_id=block.id,
        source_sha256=block.sha256,
        parts=tuple(ReadabilityPart(text=text, role="bullet") for text in marked.split("|")),
    )

    with pytest.raises(ValueError, match="preserve complete quotations, code, citations"):
        validated_readability_paragraphs(document, ir, [proposal])


@pytest.mark.parametrize(
    "sentences",
    [
        ("Cache reads take 1 µs.", "Memory reads take 100 ns."),
        ('The guide says "measure latency".', 'The report says "select storage".'),
        ("The guide says 'we don't have data'.", "The report says ‘the service isn’t ready’."),
        ("Preserve `LIMIT 10` in queries.", "Preserve `sample_rate=1e-6` in configuration."),
        ("Measurements remain 12.3 ± 0.4 ms.", "The budget remains $1,250.00."),
        ("The guide cites [1, 2].", "The report cites [3, 4]."),
        ("🙂 Cache reads take 1 µs.", "Disk reads take 10 ms."),
        ("The response is 1 234.50 ms.", "The exposure is 1 1/2 h."),
        ("The calibration is 1.234(5) ms.", "The range is 1–2 ms."),
        ("The acceleration is 9.81 m / s².", "The force is 3 kg · m / s²."),
        ("The range is 12.3 ms ± 0.4 ms.", "The estimate is 1.2 x 10^3 s."),
    ],
    ids=[
        "quantities",
        "quotations",
        "single-quoted-contractions",
        "code",
        "uncertainty-and-currency",
        "citations",
        "utf16",
        "grouped-and-mixed",
        "parenthetical-and-range",
        "spaced-compound-units",
        "complete-uncertainty-and-scientific",
    ],
)
def test_layout_can_break_between_complete_source_literals(sentences: tuple[str, str]) -> None:
    document = _prose_document((" ".join(sentences),))
    ir = readability_document(document, title="Complete literals")
    block = ir.blocks[0]
    proposal = ReadabilityProposal(
        block_id=block.id,
        source_sha256=block.sha256,
        parts=tuple(ReadabilityPart(text=text, role="bullet") for text in sentences),
    )

    paragraphs = validated_readability_paragraphs(document, ir, [proposal])

    assert tuple(part.text for part in paragraphs[0].parts) == sentences


class _RepairingLiteralEditor(_LayoutEditor):
    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        self.calls += 1
        self.feedbacks.append(feedback)
        if previous:
            return previous
        block = document.blocks[0]
        parts = (
            (
                'Operators say "measure latency',
                'then select storage" before release. Operators say "keep the uncertainty" during review.',
            )
            if self.calls == 1
            else (
                'Operators say "measure latency then select storage" before release.',
                'Operators say "keep the uncertainty" during review.',
            )
        )
        return [
            ReadabilityProposal(
                block_id=block.id,
                source_sha256=block.sha256,
                parts=tuple(ReadabilityPart(text=text, role="bullet") for text in parts),
            )
        ]


async def test_literal_boundary_failure_can_be_repaired_then_converges() -> None:
    source = 'Operators say "measure latency then select storage" before release. Operators say "keep the uncertainty" during review.'
    document = _prose_document((source,))
    editor = _RepairingLiteralEditor()

    plan = await plan_readability(document, title="Quoted instructions", generator=editor)

    assert plan is not None
    assert plan.passes == editor.calls == 3
    assert "preserve complete quotations" in editor.feedbacks[1]
    assert tuple(part.text for part in plan.paragraphs[0].parts) == (
        'Operators say "measure latency then select storage" before release.',
        'Operators say "keep the uncertainty" during review.',
    )


@pytest.mark.parametrize("version", [1, 2])
async def test_saved_checkpoint_cannot_split_a_literal_on_write_or_readback(version: int) -> None:
    before, after = _fixture("before"), _fixture("after")
    plan = await _plan()
    projected = deepcopy(before)
    project_readable_tables(projected, plan.tables, applied=False)
    project_readable_paragraphs(projected, plan.paragraphs, applied=False)
    assert document_digest(projected) == plan.invariant_sha256
    requests = readable_paragraph_requests(
        plan.paragraphs, grouped_lists=version != 1
    ) + readable_table_requests(before, plan.tables)
    for document, starts in ((before, (19, 19)), (projected, (19, 19)), (after, (19, 68))):
        for start, old, new in zip(
            starts, ("Cache reads", "Disk reads"), ('"ache reads', '"isk reads'), strict=True
        ):
            paragraph = obj(_paragraph(document, start)["paragraph"])
            run = obj(obj(array(paragraph["elements"])[0])["textRun"])
            run["content"] = str(run["content"]).replace(old, new)
    target = plan.paragraphs[0]
    source = deepcopy(_paragraph(before, 19))
    parts = (
        target.parts[0].model_copy(
            update={"text": target.parts[0].text.replace("Cache reads", '"ache reads')}
        ),
        target.parts[1],
        target.parts[2].model_copy(
            update={"text": target.parts[2].text.replace("Disk reads", '"isk reads')}
        ),
    )
    paragraphs = (
        target.model_copy(update={"source": source, "parts": parts}),
        *plan.paragraphs[1:],
    )
    checkpoint = plan.model_copy(
        update={
            "version": version,
            "paragraphs": paragraphs,
            "source_sha256": document_digest(before),
            "invariant_sha256": document_digest(projected),
            "requests": requests,
        }
    )

    assert not verify_readability(after, checkpoint)
    with pytest.raises(ValueError, match="preserve complete quotations"):
        readable_paragraph_requests(paragraphs, grouped_lists=version != 1)

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(before)

    async def write(_identifier: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("Saved checkpoints must preserve complete literals before any write")

    with pytest.raises(OutputConflictError):
        await ReadabilityProvider(read, write).apply(
            document_id=FileId("fixture"), checkpoint=checkpoint
        )


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize(
    "marked",
    [
        "The response remains 1|234.50 ms under load.",
        "The exposure remains 1|1/2 h for each sample.",
        "The calibration remains 1.234(5)|ms under load.",
        "The acceleration remains 9.81 m|/ s² under load.",
        "The acceleration remains 9.81 m /|s² under load.",
        "The report states 'we don't have|enough data' before release.",
        "The report states ‘the team’s samples|contain enough data’ before release.",
    ],
    ids=[
        "grouped-number",
        "mixed-fraction",
        "parenthetical-uncertainty",
        "spaced-unit-operator",
        "spaced-unit-denominator",
        "quoted-contraction",
        "quoted-possessive",
    ],
)
async def test_saved_literal_split_is_rejected_before_any_provider_write(
    version: int, marked: str
) -> None:
    document = _prose_document((marked.replace("|", " "),))
    source = _paragraph(document, 1)
    source_range = readability_document(document, title="Numeric checkpoint").blocks[0].source_range
    assert source_range is not None
    target = ReadabilityParagraph(
        tab_id=source_range.tab_id,
        start=1,
        end=source_range.end_index,
        source=source,
        text_style={},
        parts=tuple(ReadabilityPart(text=text, role="bullet") for text in marked.split("|")),
    )
    translation = str.maketrans(string.digits + "'‘’", "x" * 13)
    legacy_request_document = _prose_document((marked.replace("|", " ").translate(translation),))
    legacy_request_target = target.model_copy(
        update={
            "source": _paragraph(legacy_request_document, 1),
            "parts": tuple(
                part.model_copy(update={"text": part.text.translate(translation)})
                for part in target.parts
            ),
        }
    )
    checkpoint = PlannedReadabilityCheckpoint(
        source_revision_id=DocsRevisionId(str(document["revisionId"])),
        source_sha256=document_digest(document),
        invariant_sha256=document_digest(document),
        original_lists={str(target.tab_id): {}},
        paragraphs=(target,),
        requests=readable_paragraph_requests((legacy_request_target,), grouped_lists=version != 1),
    ).model_copy(update={"version": version})

    assert not verify_readability(document, checkpoint)

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_identifier: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("Reject literal splits before writing saved checkpoints")

    with pytest.raises(OutputConflictError):
        await ReadabilityProvider(read, write).apply(
            document_id=FileId("fixture"), checkpoint=checkpoint
        )
