from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.errors import ModelUnavailableError, OutputConflictError
from app.document_transform.readability import plan_readability, verify_readability
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.readability_types import ReadabilityCheckpoint
from app.identifiers import FileId
from tests.test_document_readability import _LayoutEditor
from tests.test_readability_body_tables import _body_readback
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    ("rows", "headers", "numeric"),
    [
        ((("Measurement",), ("1/2 µs",), ("1.2300 ± 0.0040 ms",)), 1, True),
        ((("Latency",), ("Milliseconds",), ("1.2300 ms",)), 2, True),
        ((("1/2 µs",), ("—",), ("1.20e-7 s",)), 0, True),
        ((("USD 1,234.50",),), 0, True),
        ((("Read only",), ("Read and write",)), 0, False),
        ((("",), ("",)), 0, False),
        ((("Measurement",),), 1, False),
    ],
    ids=[
        "header",
        "multirow-header",
        "body-quantities",
        "single-cell",
        "text",
        "empty",
        "header-only",
    ],
)
def test_single_column_tables_receive_the_same_source_bound_layout(
    rows: tuple[tuple[str, ...], ...], headers: int, *, numeric: bool
) -> None:
    document = _native_table(rows)
    table = obj(content(next(bodies(document))[1])[0])
    for value in array(obj(table["table"])["tableRows"])[:headers]:
        obj(value)["tableRowStyle"] = {"tableHeader": True}
    original = deepcopy(document)

    targets = plan_readable_tables(document)

    assert len(targets) == 1
    target = targets[0]
    assert target.widths == (9360,)
    assert target.numeric == ({0} if numeric else set())
    assert target.header is bool(headers)
    assert target.header_rows == (headers or 1)
    requests = readable_table_requests(document, targets)
    assert [
        obj(obj(request["updateParagraphStyle"])["paragraphStyle"])["alignment"]
        for request in requests
        if "updateParagraphStyle" in request
    ] == ["END" if numeric else "START"] * len(rows)
    assert [
        obj(obj(request["updateTextStyle"])["textStyle"])["bold"]
        for request in requests
        if "updateTextStyle" in request
    ] == [index < headers for index in range(len(rows)) if rows[index][0]]
    assert document == original


@pytest.mark.parametrize("span", ["rowSpan", "columnSpan"])
def test_single_column_support_keeps_merged_cells_untouched(span: str) -> None:
    document = _native_table((("Shared label",), ("Retained value",)))
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    cell = obj(array(obj(array(table["tableRows"])[0])["tableCells"])[0])
    cell["tableCellStyle"] = {span: 2}
    original = deepcopy(document)

    assert not plan_readable_tables(document)
    assert document == original


async def test_single_column_uncertain_write_recovers_without_replaying_edits() -> None:
    document = _native_table((("Read only",), ("Read and write",)))
    document["revisionId"] = "r1"
    plan = await plan_readability(document, title="Single column", generator=_LayoutEditor())
    assert plan is not None
    assert len(plan.tables) == 1
    writes = []

    async def read(_document_id: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        writes.append(True)
        after = _body_readback(document, plan.tables[0])
        document.clear()
        document.update(after)
        raise ModelUnavailableError

    provider = ReadabilityProvider(read, write)
    with pytest.raises(ModelUnavailableError):
        await provider.apply(document_id=FileId("single-column"), checkpoint=plan)
    completed = await provider.apply(document_id=FileId("single-column"), checkpoint=plan)
    assert verify_readability(document, completed)
    assert (
        await provider.apply(document_id=FileId("single-column"), checkpoint=completed) == completed
    )
    assert writes == [True]
    repeated = await plan_readability(document, title="Single column", generator=_LayoutEditor())
    assert repeated is None or not repeated.requests
    document["revisionId"] = "concurrent-revision"
    with pytest.raises(OutputConflictError):
        await provider.apply(document_id=FileId("single-column"), checkpoint=completed)
