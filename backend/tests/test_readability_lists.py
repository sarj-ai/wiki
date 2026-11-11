from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.errors import OutputConflictError
from app.document_transform.ir import utf16_length
from app.document_transform.readability import (
    plan_readability,
    readability_invariant_sha256,
    verify_readability,
)
from app.document_transform.readability_json import array, bodies, document_digest, obj
from app.document_transform.readability_paragraphs import (
    readability_document,
    readable_paragraph_requests,
    validated_readability_paragraphs,
)
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_tables import readable_table_requests
from app.document_transform.readability_types import (
    CURRENT_READABILITY_VERSION,
    ReadabilityCheckpoint,
)
from app.identifiers import FileId
from tests.test_document_readability import _fixture, _LayoutEditor, _paragraph, _plan


@pytest.mark.parametrize(
    ("role", "mutation"),
    [
        ("numbered", {"startNumber": 7}),
        ("numbered", {"startNumber": 0}),
        ("numbered", {"glyphFormat": "incorrect"}),
        ("numbered", {"glyphFormat": "%0)"}),
        ("numbered", {"glyphSymbol": "●"}),
        ("bullet", {"glyphSymbol": "☐"}),
        ("bullet", {"glyphFormat": ""}),
        ("bullet", {"glyphFormat": "unplanned %0"}),
    ],
)
async def test_readback_rejects_wrong_native_marker_values(
    role: str, mutation: dict[str, JsonValue]
) -> None:
    plan = await _plan()
    document = _fixture("after")
    start = 91 if role == "numbered" else 19
    tab = next(bodies(document))[1]
    identifier = str(obj(obj(_paragraph(document, start)["paragraph"])["bullet"])["listId"])
    definition = obj(obj(tab["lists"])[identifier])
    obj(array(obj(definition["listProperties"])["nestingLevels"])[0]).update(mutation)

    assert not verify_readability(document, plan)


@pytest.mark.parametrize("group", [0, 1])
async def test_adjacent_items_cannot_silently_restart_as_separate_native_lists(group: int) -> None:
    plan = await _plan()
    document = _fixture("after")
    tab = next(bodies(document))[1]
    target = plan.paragraphs[group]
    start = target.start + utf16_length(target.parts[0].text) + 1
    bullet = obj(obj(_paragraph(document, start)["paragraph"])["bullet"])
    lists = obj(tab["lists"])
    lists["restarted"] = deepcopy(lists[str(bullet["listId"])])
    bullet["listId"] = "restarted"

    assert not verify_readability(document, plan)


async def test_generated_list_cannot_extend_an_authored_list() -> None:
    plan = await _plan()
    document = _fixture("after")
    tab = next(bodies(document))[1]
    identifier = str(obj(obj(_paragraph(document, 19)["paragraph"])["bullet"])["listId"])
    originals = deepcopy(plan.original_lists)
    originals["t.0"][identifier] = deepcopy(obj(tab["lists"])[identifier])

    assert not verify_readability(document, plan.model_copy(update={"original_lists": originals}))


async def test_proposals_next_to_authored_lists_are_rejected_before_writing() -> None:
    document = _fixture("before")
    obj(_paragraph(document, 1)["paragraph"])["bullet"] = {"listId": "authored"}
    editor = _LayoutEditor()
    ir = readability_document(document, title="Keep authored lists")
    proposals = await editor.plan_readability(ir, previous=[])

    with pytest.raises(ValueError, match="Preserve authored lists"):
        validated_readability_paragraphs(document, ir, proposals)


async def test_native_list_requests_cover_each_complete_group_once() -> None:
    plan = await _plan()
    assert plan.version == CURRENT_READABILITY_VERSION
    assert [
        request["createParagraphBullets"]
        for request in plan.requests
        if "createParagraphBullets" in request
    ] == [
        {
            "range": {"startIndex": 19, "endIndex": 90, "tabId": "t.0"},
            "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
        },
        {
            "range": {"startIndex": 91, "endIndex": 171, "tabId": "t.0"},
            "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
        },
    ]


async def test_version_one_checkpoint_retries_its_original_per_item_requests() -> None:
    before, after = _fixture("before"), _fixture("after", version=1)
    plan = await _plan()
    paragraphs = tuple(reversed(plan.paragraphs))
    requests = readable_paragraph_requests(
        paragraphs, grouped_lists=False
    ) + readable_table_requests(before, plan.tables, version=1)
    legacy_bullets = [
        obj(obj(request["createParagraphBullets"])["range"])
        for request in requests
        if "createParagraphBullets" in request
    ]
    assert [(value["startIndex"], value["endIndex"]) for value in legacy_bullets] == [
        (91, 119),
        (120, 141),
        (142, 171),
        (19, 41),
        (42, 67),
        (68, 90),
    ]
    legacy = plan.model_copy(update={"version": 1, "paragraphs": paragraphs, "requests": requests})
    legacy = legacy.model_copy(
        update={"invariant_sha256": readability_invariant_sha256(before, legacy, applied=False)}
    )
    current = before
    writes = []

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(current)

    async def write(_identifier: FileId, checkpoint: ReadabilityCheckpoint) -> None:
        nonlocal current
        writes.append(checkpoint.requests)
        current = after

    provider = ReadabilityProvider(read, write)
    completed = await provider.apply(document_id=FileId("fixture"), checkpoint=legacy)
    assert completed.complete
    assert completed.version == 1
    assert writes == [requests]
    assert await provider.apply(document_id=FileId("fixture"), checkpoint=legacy) == completed


async def test_legacy_checkpoint_next_to_authored_list_fails_before_mutation() -> None:
    document = _fixture("before")
    plan = await _plan()
    obj(_paragraph(document, 1)["paragraph"])["bullet"] = {"listId": "authored"}
    legacy = plan.model_copy(update={"version": 1, "source_sha256": document_digest(document)})

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_identifier: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("An old checkpoint must not extend an authored list")

    with pytest.raises(OutputConflictError):
        await ReadabilityProvider(read, write).apply(
            document_id=FileId("fixture"), checkpoint=legacy
        )


async def test_production_request_limit_drops_both_siblings_when_one_does_not_fit() -> None:
    fact = "Cache reads take 1 µs."
    dense_sections = tuple(
        part for _ in range(19) for part in ("Operational notes", " ".join([fact] * 32))
    )
    source = (
        *dense_sections,
        "Operational notes",
        " ".join([fact] * 10),
        "Operational notes",
        "Operational notes",
        fact,
        "Cache reads take 100 ns.",
    )
    document = _prose_document(source)
    ir = readability_document(document, title="Adjacent facts")
    proposals = await _LayoutEditor().plan_readability(ir, previous=[])
    paragraphs = validated_readability_paragraphs(document, ir, proposals)
    assert len(readable_paragraph_requests(paragraphs)) == 2501
    plan = await plan_readability(document, title="Bounded layout", generator=_LayoutEditor())
    assert plan is not None
    assert len(plan.requests) == 2496
    assert plan.paragraphs == paragraphs[:-2]


@pytest.mark.parametrize(
    "procedure",
    [
        "First, measure latency. Next, select storage. Finally, verify the result.",
        "First, measure. Next, verify.",
    ],
)
async def test_concise_procedural_steps_can_improve_the_document_layout(procedure: str) -> None:
    document = _prose_document(("Operational notes", procedure))
    original = deepcopy(document)
    editor = _LayoutEditor()

    plan = await plan_readability(document, title="Operational notes", generator=editor)

    assert plan is not None
    assert plan.passes == editor.calls == 2
    assert len(plan.paragraphs) == 2
    assert all(part.role == "numbered" for part in plan.paragraphs[1].parts)
    assert " ".join(part.text for part in plan.paragraphs[1].parts) == procedure
    assert document == original


def _prose_document(texts: tuple[str, ...]) -> dict[str, JsonValue]:
    document = _fixture("before")
    tab = next(bodies(document))[1]
    template = deepcopy(_paragraph(document, 19))
    elements: list[JsonValue] = []
    start = 1
    for prose in texts:
        text = prose + "\n"
        element = deepcopy(template)
        end = start + utf16_length(text)
        element.update({"startIndex": start, "endIndex": end})
        run = obj(array(obj(element["paragraph"])["elements"])[0])
        run.update({"startIndex": start, "endIndex": end})
        obj(run["textRun"])["content"] = text
        elements.append(element)
        start = end
    obj(tab["body"])["content"] = elements
    return document
