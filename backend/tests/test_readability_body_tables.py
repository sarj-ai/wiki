from copy import deepcopy

from pydantic import JsonValue
import pytest

from app.document_transform.errors import ModelUnavailableError
from app.document_transform.readability import (
    plan_readability,
    readability_invariant_sha256,
    verify_readability,
)
from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.readability_types import (
    CURRENT_READABILITY_VERSION,
    PlannedReadabilityCheckpoint,
    ReadabilityCheckpoint,
    ReadabilityTable,
    ReadabilityVersion,
)
from app.identifiers import FileId
from tests.test_document_readability import _LayoutEditor
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    "rows",
    [
        (("Cache", "Read only"), ("Disk", "Read and write")),
        (("1/2 µs", "$1,250.00"), ("12.3 ± 0.4 ms", "1.20e-7 s")),
        (("Status", "Ready"),),
        (("", ""), ("", "")),
    ],
    ids=["text-only", "quantities", "single-row", "empty-cells"],
)
def test_body_tables_receive_cell_formatting_without_inventing_a_header(
    rows: tuple[tuple[str, ...], ...],
) -> None:
    document = _native_table(rows)
    original = deepcopy(document)
    targets = plan_readable_tables(document)

    assert len(targets) == 1
    assert not targets[0].header
    requests = readable_table_requests(document, targets)
    cells = [
        obj(request["updateTableCellStyle"])
        for request in requests
        if "updateTableCellStyle" in request
    ]
    assert len(cells) == len(rows)
    for cell in cells:
        style = obj(cell["tableCellStyle"])
        assert "paddingLeft" not in style
        assert "paddingRight" not in style
        assert style["paddingTop"] == {"magnitude": 4, "unit": "PT"}
        assert style["contentAlignment"] == "TOP"
        assert obj(style["borderBottom"])["width"] == {"magnitude": 0.5, "unit": "PT"}
    if len(cells) > 1:
        assert (
            obj(cells[0]["tableCellStyle"])["backgroundColor"]
            != obj(cells[1]["tableCellStyle"])["backgroundColor"]
        )
    assert not any(
        "updateTextStyle" in request and "bold" in str(obj(request["updateTextStyle"])["fields"])
        for request in requests
    )
    assert not any("pinTableHeaderRows" in request for request in requests)
    assert document == original


@pytest.mark.parametrize("header", [False, True])
def test_older_table_checkpoints_reconstruct_their_original_cell_requests(*, header: bool) -> None:
    document = _native_table((("Operation", "Value"), ("Cache", "1/2 µs")))
    target = plan_readable_tables(document)[0].model_copy(update={"header": header})
    legacy_data: dict[str, JsonValue] = target.model_dump(mode="json")
    legacy_data.pop("style_cells", None)
    legacy = ReadabilityTable.model_validate(legacy_data)
    requests = readable_table_requests(document, (legacy,))

    assert any("updateTableCellStyle" in request for request in requests) is header
    if header:
        assert requests == readable_table_requests(document, (target,))


def _body_readback(
    document: dict[str, JsonValue], target: ReadabilityTable
) -> dict[str, JsonValue]:
    result = deepcopy(document)
    result["revisionId"] = "r2"
    table = obj(obj(content(next(bodies(result))[1])[0])["table"])
    table["tableStyle"] = {
        "tableColumnProperties": [
            {"widthType": "FIXED_WIDTH", "width": {"magnitude": width / 20, "unit": "PT"}}
            for width in target.widths
        ]
    }
    for index, value in enumerate(array(table["tableRows"])):
        for cell_value in array(obj(value)["tableCells"]):
            cell = obj(cell_value)
            color = 245 / 255 if index % 2 == 0 else 1
            border: dict[str, JsonValue] = {
                "color": {
                    "color": {"rgbColor": dict.fromkeys(("red", "green", "blue"), 229 / 255)}
                },
                "width": {"magnitude": 0.5, "unit": "PT"},
                "dashStyle": "SOLID",
            }
            cell["tableCellStyle"] = {
                **obj(cell.get("tableCellStyle", {})),
                **dict.fromkeys(("borderTop", "borderBottom", "borderLeft", "borderRight"), border),
                "backgroundColor": {
                    "color": {"rgbColor": dict.fromkeys(("red", "green", "blue"), color)}
                },
                "paddingTop": {"magnitude": 4, "unit": "PT"},
                "paddingBottom": {"magnitude": 4, "unit": "PT"},
                "contentAlignment": "TOP",
            }
            for value_part in array(cell["content"]):
                paragraph = obj(obj(value_part)["paragraph"])
                paragraph["paragraphStyle"] = {
                    "alignment": "START",
                    "lineSpacing": 100,
                    "spaceAbove": {"magnitude": 0, "unit": "PT"},
                    "spaceBelow": {"magnitude": 2, "unit": "PT"},
                }
                for run in array(paragraph["elements"]):
                    obj(obj(run)["textRun"])["textStyle"] = {
                        "weightedFontFamily": {"fontFamily": "Nunito"},
                        "fontSize": {"magnitude": 10, "unit": "PT"},
                    }
    return result


@pytest.mark.parametrize(
    "corruption",
    [None, "paddingLeft", "borderBottom", "backgroundColor", "contentAlignment", "text"],
)
async def test_body_table_readback_checks_cell_styles_and_every_source_fact(
    corruption: str | None,
) -> None:
    before = _native_table((("Cache", "Read only"), ("Disk", "Read and write")))
    before["revisionId"] = "r1"
    plan = await plan_readability(before, title="Body table", generator=_LayoutEditor())
    assert plan is not None
    after = _body_readback(before, plan.tables[0])
    table = obj(obj(content(next(bodies(after))[1])[0])["table"])
    cell = obj(array(obj(array(table["tableRows"])[0])["tableCells"])[0])
    style = obj(cell["tableCellStyle"])
    if corruption == "text":
        paragraph = obj(obj(array(cell["content"])[0])["paragraph"])
        obj(obj(array(paragraph["elements"])[0])["textRun"])["content"] = "Other\n"
    elif corruption is not None:
        style[corruption] = "MIDDLE" if corruption == "contentAlignment" else {}
    original = deepcopy(after)

    assert verify_readability(after, plan) is (corruption is None)
    assert after == original


async def test_body_table_uncertain_write_recovers_once_and_repeated_formatting_converges() -> None:
    document = _native_table((("Cache", "Read only"), ("Disk", "Read and write")))
    document["revisionId"] = "r1"
    editor = _LayoutEditor()
    plan = await plan_readability(document, title="Body table", generator=editor)
    assert plan is not None
    writes = []

    async def read(_document_id: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_document_id: FileId, checkpoint: ReadabilityCheckpoint) -> None:
        writes.append(checkpoint)
        after = _body_readback(document, checkpoint.tables[0])
        document.clear()
        document.update(after)
        raise ModelUnavailableError

    provider = ReadabilityProvider(read, write)
    with pytest.raises(ModelUnavailableError):
        await provider.apply(document_id=FileId("body-table"), checkpoint=plan)
    completed = await provider.apply(document_id=FileId("body-table"), checkpoint=plan)
    assert completed.complete
    assert await provider.apply(document_id=FileId("body-table"), checkpoint=completed) == completed
    assert await plan_readability(document, title="Body table", generator=editor) is None
    assert writes == [plan]
    assert editor.calls == 0


def _first_cell_style(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    table = obj(obj(content(next(bodies(document))[1])[0])["table"])
    cell = obj(array(obj(array(table["tableRows"])[0])["tableCells"])[0])
    return obj(cell.setdefault("tableCellStyle", {}))


@pytest.mark.parametrize("horizontal", [None, "paddingLeft", "paddingRight", "paddingStart"])
async def test_new_table_plan_preserves_omitted_explicit_and_unknown_horizontal_spacing(
    horizontal: str | None,
) -> None:
    document = _native_table((("Cache", "Read only"), ("Disk", "Read and write")))
    document["revisionId"] = "r1"
    next(bodies(document))[1]["footers"] = {
        "source-footer": {
            "footerId": "source-footer",
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Protected footer\n"}}]}}
            ],
        }
    }
    if horizontal is not None:
        _first_cell_style(document)[horizontal] = {
            "magnitude": 7,
            "unit": "PT",
            "sourceExtension": "preserve",
        }
    before = deepcopy(document)
    plan = await plan_readability(document, title="Preserve spacing", generator=_LayoutEditor())
    assert plan is not None
    assert plan.version == CURRENT_READABILITY_VERSION
    for request in plan.requests:
        if "updateTableCellStyle" in request:
            update = obj(request["updateTableCellStyle"])
            assert "paddingLeft" not in obj(update["tableCellStyle"])
            assert "paddingRight" not in str(update["fields"])
    writes = []

    async def read(_identifier: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_identifier: FileId, checkpoint: ReadabilityCheckpoint) -> None:
        writes.append(checkpoint.requests)
        after = _body_readback(document, checkpoint.tables[0])
        document.clear()
        document.update(after)

    provider = ReadabilityProvider(read, write)
    completed = await provider.apply(document_id=FileId("spacing"), checkpoint=plan)
    assert completed.complete
    assert await provider.apply(document_id=FileId("spacing"), checkpoint=completed) == completed
    assert writes == [plan.requests]
    assert verify_readability(document, completed)
    altered = deepcopy(document)
    _first_cell_style(altered)[horizontal or "paddingLeft"] = {"magnitude": 6, "unit": "PT"}
    assert not verify_readability(altered, completed)
    assert _first_cell_style(before).get(horizontal or "paddingLeft") == (
        _first_cell_style(document).get(horizontal or "paddingLeft")
    )
    footer_changed = deepcopy(document)
    next(bodies(footer_changed))[1]["footers"] = {}
    assert not verify_readability(footer_changed, completed)


@pytest.mark.parametrize("version", [1, 2])
async def test_legacy_table_plan_retains_exact_horizontal_requests_and_strict_readback(
    version: ReadabilityVersion,
) -> None:
    before = _native_table((("Cache", "Read only"), ("Disk", "Read and write")))
    before["revisionId"] = "r1"
    plan = await plan_readability(before, title="Legacy spacing", generator=_LayoutEditor())
    assert plan is not None
    requests = readable_table_requests(before, plan.tables, version=version)
    legacy = plan.model_copy(update={"version": version, "requests": requests})
    legacy = legacy.model_copy(
        update={"invariant_sha256": readability_invariant_sha256(before, legacy, applied=False)}
    )
    after = _body_readback(before, legacy.tables[0])
    table = obj(obj(content(next(bodies(after))[1])[0])["table"])
    for row in array(table["tableRows"]):
        for cell in array(obj(row)["tableCells"]):
            style = obj(obj(cell)["tableCellStyle"])
            style["paddingLeft"] = {"magnitude": 6, "unit": "PT"}
            style["paddingRight"] = {"magnitude": 6, "unit": "PT"}
    assert verify_readability(after, legacy)
    assert legacy.requests == readable_table_requests(before, legacy.tables, version=version)
    assert all(
        obj(obj(request["updateTableCellStyle"])["tableCellStyle"])["paddingLeft"]
        == {"magnitude": 6, "unit": "PT"}
        for request in requests
        if "updateTableCellStyle" in request
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
    completed = await provider.apply(document_id=FileId("legacy-spacing"), checkpoint=legacy)
    assert completed.complete
    assert completed.version == version
    assert writes == [requests]
    assert (
        await provider.apply(document_id=FileId("legacy-spacing"), checkpoint=legacy) == completed
    )
    _first_cell_style(after).pop("paddingLeft")
    assert not verify_readability(after, legacy)
    payload = legacy.model_dump(mode="json")
    payload.pop("version")
    assert PlannedReadabilityCheckpoint.model_validate(payload).version == 2
