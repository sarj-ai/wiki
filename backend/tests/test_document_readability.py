from copy import deepcopy
from pathlib import Path
from typing import Literal, override
import uuid

from pydantic import JsonValue, RootModel
import pytest

from app.document_transform.errors import (
    ModelUnavailableError,
    OutputConflictError,
    OutputVerificationError,
)
from app.document_transform.ir import DocumentIR, utf16_length
from app.document_transform.models import PatchGenerator
from app.document_transform.readability import plan_readability, verify_readability
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_paragraphs import (
    readability_document,
    validated_readability_paragraphs,
)
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_types import (
    ReadabilityCheckpoint,
    ReadabilityPart,
    ReadabilityProposal,
)
from app.document_transform.schemas import TextPatch, TransformPhase
from app.identifiers import FileId


_DATA = Path(__file__).parent / "data" / "readability"


class _DocumentFixture(RootModel[dict[str, JsonValue]]):
    pass


def _fixture(stage: Literal["before", "after"]) -> dict[str, JsonValue]:
    return _DocumentFixture.model_validate_json((_DATA / f"{stage}.json").read_text()).root


def _paragraph(document: dict[str, JsonValue], start: int) -> dict[str, JsonValue]:
    return next(
        obj(value)
        for _, tab in bodies(document)
        for value in content(tab)
        if obj(value).get("startIndex") == start
    )


class _LayoutEditor(PatchGenerator):
    supports_readability_planning = True

    def __init__(self, *, unavailable: bool = False, invalid: bool = False) -> None:
        self.calls = 0
        self.feedbacks: list[str] = []
        self.unavailable = unavailable
        self.invalid = invalid

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("fixture-pro",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        raise NotImplementedError

    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        self.feedbacks.append(feedback)
        self.calls += 1
        if self.unavailable:
            raise ModelUnavailableError
        if previous:
            return previous
        proposals = []
        for block in document.blocks:
            role: Literal["bullet", "numbered", "heading2"]
            if block.text.startswith("Cache reads take"):
                role = "bullet"
            elif block.text.startswith("First, measure"):
                role = "numbered"
            elif block.text == "Operational notes":
                role = "heading2"
            else:
                continue
            chunks = block.text.split(". ") if role != "heading2" else [block.text]
            parts = [
                ReadabilityPart(text=text + "." if index < len(chunks) - 1 else text, role=role)
                for index, text in enumerate(chunks)
            ]
            if self.invalid:
                parts[0] = parts[0].model_copy(update={"text": "A new and invented fact."})
            proposals.append(
                ReadabilityProposal(
                    block_id=block.id, source_sha256=block.sha256, parts=tuple(parts)
                )
            )
        return proposals


async def _plan(editor: _LayoutEditor | None = None) -> ReadabilityCheckpoint:
    plan = await plan_readability(
        _fixture("before"), title="Readability fixture", generator=editor or _LayoutEditor()
    )
    assert plan is not None
    return plan


async def test_real_google_docs_readback_verifies_native_lists_heading_and_content_fitted_table() -> (
    None
):
    editor = _LayoutEditor()
    plan = await _plan(editor)
    assert editor.calls == plan.passes == 2
    assert len(plan.paragraphs) == 3
    assert len(plan.tables) == 1
    assert plan.tables[0].numeric == {1}
    assert plan.tables[0].widths == (1717, 1663, 5980)
    assert verify_readability(_fixture("after"), plan)
    assert not verify_readability(_fixture("before"), plan)
    assert "NUMBERED_DECIMAL_ALPHA_ROMAN" in str(plan.requests)
    assert "columnWidth" not in str(plan.requests)


@pytest.mark.parametrize(
    "corruption",
    [
        "number",
        "inline_object",
        "list_type",
        "table_width",
        "table_font",
        "table_link",
        "unrelated_text",
        "section",
        "original_list",
    ],
)
async def test_readability_rejects_unplanned_changes_anywhere_in_the_document(
    corruption: str,
) -> None:
    plan = await _plan()
    document = _fixture("after")
    tab = next(bodies(document))[1]
    first = obj(_paragraph(document, 19)["paragraph"])
    run = obj(obj(array(first["elements"])[0])["textRun"])
    table = obj(_paragraph(document, 247)["table"])
    cell = obj(array(obj(array(table["tableRows"])[1])["tableCells"])[1])
    table_run = obj(
        obj(array(obj(obj(array(cell["content"])[0])["paragraph"])["elements"])[0])["textRun"]
    )
    match corruption:
        case "number":
            run["content"] = str(run["content"]).replace("1 µs", "9 µs")
        case "inline_object":
            array(first["elements"]).append(
                {"inlineObjectElement": {"inlineObjectId": "unplanned"}}
            )
        case "list_type":
            identifier = str(obj(first["bullet"])["listId"])
            level = obj(
                array(obj(obj(obj(tab["lists"])[identifier])["listProperties"])["nestingLevels"])[0]
            )
            level["glyphType"] = "DECIMAL"
        case "table_width":
            obj(array(obj(table["tableStyle"])["tableColumnProperties"])[0])["width"] = {
                "magnitude": 400,
                "unit": "PT",
            }
        case "table_font":
            obj(table_run["textStyle"])["weightedFontFamily"] = {"fontFamily": "Arial"}
        case "table_link":
            obj(table_run["textStyle"])["link"] = {"url": "https://example.test/unplanned"}
        case "unrelated_text":
            other = obj(
                obj(array(obj(_paragraph(document, 190)["paragraph"])["elements"])[0])["textRun"]
            )
            other["content"] = str(other["content"]).replace("storage", "network")
        case "section":
            content(tab).append({"startIndex": 999, "endIndex": 1000, "sectionBreak": {}})
        case _:
            obj(tab["lists"])["unrelated"] = {
                "listProperties": {"nestingLevels": [{"glyphType": "DECIMAL"}]}
            }
            obj(_paragraph(document, 190)["paragraph"])["bullet"] = {"listId": "unrelated"}
    assert not verify_readability(document, plan)


@pytest.mark.parametrize("case", range(100))
async def test_100_paraphrase_or_numeric_changes_cannot_hide_in_a_layout_proposal(
    case: int,
) -> None:
    document = _fixture("before")
    ir = readability_document(document, title="Readability fixture")
    block = next(block for block in ir.blocks if block.text.startswith("Cache reads"))
    proposal = ReadabilityProposal(
        block_id=block.id,
        source_sha256=block.sha256,
        parts=(ReadabilityPart(text=block.text + str(case), role="paragraph"),),
    )
    with pytest.raises(ValueError, match="preserve every source character"):
        validated_readability_paragraphs(document, ir, [proposal])


@pytest.mark.parametrize("outage", [False, True])
async def test_optional_layout_failure_retains_verified_text_and_still_improves_tables(
    outage: bool,
) -> None:
    editor = _LayoutEditor(unavailable=outage, invalid=not outage)
    plan = await _plan(editor)
    assert not plan.paragraphs
    assert len(plan.tables) == 1
    assert editor.calls == (1 if outage else 2)


async def test_provider_recovers_a_completed_write_without_reapplying_or_calling_the_model() -> (
    None
):
    plan = await _plan()
    document = _fixture("before")
    calls = []

    async def read(document_id: FileId) -> dict[str, JsonValue]:
        assert document_id == "readability-fixture"
        return deepcopy(document)

    async def write(document_id: FileId, checkpoint: ReadabilityCheckpoint) -> None:
        assert checkpoint == plan
        calls.append(document_id)
        document.clear()
        document.update(_fixture("after"))

    provider = ReadabilityProvider(read, write)
    completed = await provider.apply(document_id=FileId("readability-fixture"), checkpoint=plan)
    assert completed.complete
    assert completed.revision_id == "r2"
    assert (
        await provider.apply(document_id=FileId("readability-fixture"), checkpoint=plan)
        == completed
    )
    assert (
        await provider.apply(document_id=FileId("readability-fixture"), checkpoint=completed)
        == completed
    )
    assert calls == ["readability-fixture"]
    document["revisionId"] = "concurrent-edit"
    with pytest.raises(OutputConflictError):
        await provider.apply(document_id=FileId("readability-fixture"), checkpoint=completed)


async def test_provider_rejects_tampered_requests_before_writing() -> None:
    plan = await _plan()

    async def read(_document_id: FileId) -> dict[str, JsonValue]:
        return _fixture("before")

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("Must reject before any write")

    with pytest.raises(OutputConflictError):
        await ReadabilityProvider(read, write).apply(
            document_id=FileId("readability-fixture"),
            checkpoint=plan.model_copy(update={"requests": [{"deleteContentRange": {}}]}),
        )


async def test_provider_never_reports_success_for_an_incomplete_readback() -> None:
    plan = await _plan()

    async def read(_document_id: FileId) -> dict[str, JsonValue]:
        return _fixture("before")

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pass

    with pytest.raises(OutputVerificationError):
        await ReadabilityProvider(read, write).apply(
            document_id=FileId(str(uuid.uuid7())), checkpoint=plan
        )


class _GradualLayoutEditor(_LayoutEditor):
    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        self.feedbacks.append(feedback)
        self.calls += 1
        block = document.blocks[self.calls - 1]
        return [
            *previous,
            ReadabilityProposal(
                block_id=block.id,
                source_sha256=block.sha256,
                parts=(
                    ReadabilityPart(text="Cache reads take 1 ns.", role="bullet"),
                    ReadabilityPart(text="Disk reads take 10 ms.", role="bullet"),
                ),
            ),
        ]


async def test_layout_can_run_all_100_improving_passes_and_keeps_every_source_character() -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    template = deepcopy(_paragraph(document, 19))
    paragraphs: list[JsonValue] = []
    source = "Cache reads take 1 ns. Disk reads take 10 ms.\n"
    for index in range(100):
        element = deepcopy(template)
        start = 1 + index * utf16_length(source)
        end = start + utf16_length(source)
        element.update({"startIndex": start, "endIndex": end})
        run = obj(array(obj(element["paragraph"])["elements"])[0])
        run.update({"startIndex": start, "endIndex": end})
        obj(run["textRun"])["content"] = source
        paragraphs.append(element)
    obj(tab["body"])["content"] = paragraphs
    editor = _GradualLayoutEditor()
    plan = await plan_readability(document, title="Parallel facts", generator=editor)
    assert plan is not None
    assert plan.passes == editor.calls == 100
    assert len(plan.paragraphs) == 100
    assert all(
        " ".join(part.text for part in item.parts) + "\n" == source for item in plan.paragraphs
    )


async def test_repeated_formatting_is_a_zero_write_noop_when_the_layout_already_matches() -> None:
    document = _fixture("after")
    assert (
        await plan_readability(
            document, title="Readability fixture", generator=_LayoutEditor(unavailable=True)
        )
        is None
    )


async def test_inherited_nunito_typography_remains_eligible_for_holistic_structure() -> None:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    normal = obj(array(obj(tab["namedStyles"])["styles"])[0])
    obj(normal["textStyle"])["weightedFontFamily"] = {"fontFamily": "Nunito", "weight": 400}
    for value in content(tab):
        element = obj(value)
        if "paragraph" in element:
            for run_value in array(obj(element["paragraph"])["elements"]):
                obj(obj(obj(run_value)["textRun"]).get("textStyle", {})).pop(
                    "weightedFontFamily", None
                )
    plan = await plan_readability(document, title="Readability fixture", generator=_LayoutEditor())
    assert plan is not None
    assert len(plan.paragraphs) == 3
    assert all(
        obj(item.text_style["weightedFontFamily"])["fontFamily"] == "Nunito"
        for item in plan.paragraphs
    )


@pytest.mark.parametrize("kind", ["dateElement", "person", "richLink"])
async def test_native_date_people_and_map_pills_survive_readability_passes(kind: str) -> None:
    before = _fixture("before")
    after = _fixture("after")
    payloads: dict[str, JsonValue] = {
        "dateElement": {
            "dateElementProperties": {
                "displayText": "2026-11-10",
                "timestamp": "2026-11-10T00:00:00Z",
            }
        },
        "person": {"personProperties": {"email": "reader@example.test", "name": "Reader"}},
        "richLink": {
            "richLinkProperties": {"uri": "https://maps.google.com/?q=London", "title": "London"}
        },
    }
    for document in (before, after):
        tab = next(bodies(document))[1]
        content(tab).append(
            {
                "startIndex": 500,
                "endIndex": 502,
                "paragraph": {
                    "elements": [
                        {"startIndex": 500, "endIndex": 501, kind: deepcopy(payloads[kind])},
                        {"startIndex": 501, "endIndex": 502, "textRun": {"content": "\n"}},
                    ],
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                },
            }
        )
    plan = await plan_readability(before, title="Readability fixture", generator=_LayoutEditor())
    assert plan is not None
    assert verify_readability(after, plan)
    tab = next(bodies(after))[1]
    obj(content(tab)[-1])["paragraph"] = {"elements": [{"textRun": {"content": "Changed pill\n"}}]}
    assert not verify_readability(after, plan)
