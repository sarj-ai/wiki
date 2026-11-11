from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.errors import ModelUnavailableError, OutputConflictError
from app.document_transform.readability import plan_readability, verify_readability
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.readability_types import (
    PlannedReadabilityCheckpoint,
    ReadabilityCheckpoint,
)
from app.identifiers import FileId
from tests.test_document_readability import _fixture, _LayoutEditor, _paragraph, _plan
from tests.test_table_layout import _native_table


@pytest.mark.parametrize("headers", [1, 2, 3])
@pytest.mark.parametrize("numeric", [False, True])
def test_native_header_rows_control_typography_shading_and_numeric_alignment(
    headers: int, *, numeric: bool
) -> None:
    rows = (
        *(("Operation", "Measurement", "Notes") for _ in range(headers)),
        ("Cache", "1/2 µs" if numeric else "Read only", "Retain this qualification"),
        ("Memory", "1.20e-7 s" if numeric else "Read and write", "Keep this link label"),
    )
    document = _native_table(rows)
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    for index, value in enumerate(array(table["tableRows"])):
        obj(value)["tableRowStyle"] = {"tableHeader": index < headers}
    original = deepcopy(document)

    targets = plan_readable_tables(document)

    assert len(targets) == 1
    target = targets[0]
    assert target.header
    assert target.header_rows == headers
    assert target.numeric == ({1} if numeric else set())
    requests = readable_table_requests(document, targets)
    fonts = [
        obj(request["updateTextStyle"]) for request in requests if "updateTextStyle" in request
    ]
    assert ["bold" in obj(font["textStyle"]) for font in fonts] == [True] * (3 * headers) + [
        False
    ] * 6
    assert all(obj(font["textStyle"])["bold"] is True for font in fonts[: 3 * headers])
    backgrounds = [
        obj(obj(request["updateTableCellStyle"])["tableCellStyle"])["backgroundColor"]
        for request in requests
        if "updateTableCellStyle" in request
    ]
    assert len(backgrounds) == len(rows)
    assert all(color == backgrounds[0] for color in backgrounds[:headers])
    assert backgrounds[headers] != backgrounds[0]
    assert not any("pinTableHeaderRows" in request for request in requests)
    assert document == original


def _multirow_header_fixture(stage: str) -> dict[str, JsonValue]:
    document = _fixture("before" if stage == "before" else "after")
    rows = array(obj(_paragraph(document, 247)["table"])["tableRows"])
    for value in rows[:2]:
        obj(obj(value)["tableRowStyle"])["tableHeader"] = True
    if stage == "after":
        for value in array(obj(rows[1])["tableCells"]):
            cell = obj(value)
            obj(cell["tableCellStyle"])["backgroundColor"] = deepcopy(
                obj(obj(array(obj(rows[0])["tableCells"])[0])["tableCellStyle"])["backgroundColor"]
            )
            for part in array(cell["content"]):
                for run in array(obj(obj(part)["paragraph"])["elements"]):
                    obj(obj(obj(run)["textRun"])["textStyle"])["bold"] = True
    return document


@pytest.mark.parametrize("corruption", [None, "bold", "background", "native_header", "quantity"])
async def test_readback_verifies_every_header_and_preserves_source_bound_native_metadata(
    corruption: str | None,
) -> None:
    before = _multirow_header_fixture("before")
    after = _multirow_header_fixture("after")
    plan = await plan_readability(before, title="Native headers", generator=_LayoutEditor())
    assert plan is not None
    assert plan.tables[0].header_rows == 2
    rows = array(obj(_paragraph(after, 247)["table"])["tableRows"])
    second = obj(rows[1])
    cell = obj(array(second["tableCells"])[1])
    part = obj(array(cell["content"])[0])
    run = obj(obj(array(obj(part["paragraph"])["elements"])[0])["textRun"])
    if corruption == "bold":
        obj(run["textStyle"])["bold"] = False
    elif corruption == "background":
        obj(cell["tableCellStyle"])["backgroundColor"] = {}
    elif corruption == "native_header":
        obj(second["tableRowStyle"])["tableHeader"] = False
    elif corruption == "quantity":
        run["content"] = "1/3 µs\n"
    original = deepcopy(after)
    assert verify_readability(after, plan) is (corruption is None)
    assert after == original


async def test_native_header_checkpoint_recovers_an_uncertain_write_without_replaying_it() -> None:
    document = _multirow_header_fixture("before")
    plan = await plan_readability(document, title="Native headers", generator=_LayoutEditor())
    assert plan is not None
    writes = []

    async def read(_document_id: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        writes.append(True)
        document.clear()
        document.update(_multirow_header_fixture("after"))
        raise ModelUnavailableError

    provider = ReadabilityProvider(read, write)
    with pytest.raises(ModelUnavailableError):
        await provider.apply(document_id=FileId("native-header-qa"), checkpoint=plan)
    completed = await provider.apply(document_id=FileId("native-header-qa"), checkpoint=plan)
    assert (
        await provider.apply(document_id=FileId("native-header-qa"), checkpoint=completed)
        == completed
    )
    assert writes == [True]
    assert (
        await plan_readability(document, title="Native headers", generator=_LayoutEditor()) is None
    )
    document["revisionId"] = "concurrent-revision"
    with pytest.raises(OutputConflictError):
        await provider.apply(document_id=FileId("native-header-qa"), checkpoint=completed)


async def test_older_table_checkpoints_keep_their_single_row_requests_and_readback_proof() -> None:
    plan = await _plan()
    payload = plan.model_dump(mode="json")
    for value in array(payload["tables"]):
        obj(value).pop("header_rows")
    legacy = PlannedReadabilityCheckpoint.model_validate(payload)

    assert legacy.tables[0].header_rows == 1
    assert legacy.requests == plan.requests
    assert verify_readability(_fixture("after"), legacy)
    assert readable_table_requests(_fixture("before"), legacy.tables) == readable_table_requests(
        _fixture("before"), plan.tables
    )
