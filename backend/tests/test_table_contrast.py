from copy import deepcopy
from pathlib import Path

from pydantic import JsonValue, ValidationError
import pytest

from app.document_transform.errors import OutputConflictError
from app.document_transform.readability import verify_readability
from app.document_transform.readability_json import array, bodies, content, document_digest, obj
from app.document_transform.readability_provider import ReadabilityProvider
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.readability_types import (
    PlannedReadabilityCheckpoint,
    ReadabilityCheckpoint,
)
from app.document_transform.table_contrast import table_text_has_readable_contrast
from app.identifiers import FileId
from tests.test_readability_body_tables import _body_readback
from tests.test_table_layout import _native_table


_WHITE: dict[str, JsonValue] = {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}}
_DARK: dict[str, JsonValue] = {"color": {"rgbColor": {"red": 0.125, "green": 0.125, "blue": 0.125}}}


def _white_text(document: dict[str, JsonValue], *, inherited: bool) -> None:
    tab = next(bodies(document))[1]
    if inherited:
        tab["namedStyles"] = {
            "styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": {"foregroundColor": _WHITE}}]
        }
    table = obj(obj(content(tab)[0])["table"])
    for row in array(table["tableRows"]):
        for value in array(obj(row)["tableCells"]):
            cell = obj(value)
            obj(cell.setdefault("tableCellStyle", {}))["backgroundColor"] = _DARK
            for paragraph in array(cell["content"]):
                for run in array(obj(obj(paragraph)["paragraph"])["elements"]):
                    style = obj(obj(obj(run)["textRun"]).setdefault("textStyle", {}))
                    if not inherited:
                        style["foregroundColor"] = _WHITE


@pytest.mark.parametrize("columns", [1, 2, 3])
@pytest.mark.parametrize("inherited", [False, True])
def test_readable_dark_tables_are_preserved_when_planned_shading_hides_text(
    columns: int, *, inherited: bool
) -> None:
    document = _native_table(tuple(("1.2300 ms",) * columns for _ in range(2)))
    _white_text(document, inherited=inherited)
    original = deepcopy(document)

    assert not plan_readable_tables(document)
    assert document == original


@pytest.mark.parametrize(
    ("style", "readable"),
    [
        ({}, True),
        ({"foregroundColor": {"color": {"rgbColor": {}}}}, True),
        ({"foregroundColor": _WHITE}, False),
        ({"foregroundColor": _WHITE, "backgroundColor": _DARK}, True),
        ({"foregroundColor": _WHITE, "backgroundColor": {}}, False),
        ({"foregroundColor": {}}, False),
        ({"foregroundColor": None}, False),
        ({"backgroundColor": None}, False),
    ],
)
def test_contrast_resolves_omitted_channels_and_opaque_or_transparent_inline_backgrounds(
    style: dict[str, JsonValue], *, readable: bool
) -> None:
    assert table_text_has_readable_contrast(style, _WHITE) is readable


@pytest.mark.parametrize("ratio", [4.499, 4.501])
def test_contrast_threshold_is_not_rounded(ratio: float) -> None:
    luminance = 1.05 / ratio - 0.05
    gray = 1.055 * luminance ** (1 / 2.4) - 0.055
    style: dict[str, JsonValue] = {
        "foregroundColor": {"color": {"rgbColor": dict.fromkeys(("red", "green", "blue"), gray)}}
    }
    assert table_text_has_readable_contrast(style, _WHITE) is (ratio > 4.5)


@pytest.mark.parametrize("component", [True, -0.01, 1.01, float("nan"), float("inf")])
def test_invalid_color_components_cannot_be_treated_as_readable(component: JsonValue) -> None:
    with pytest.raises(ValidationError):
        table_text_has_readable_contrast(
            {"foregroundColor": {"color": {"rgbColor": {"red": component}}}}, _WHITE
        )


async def test_legacy_unsafe_checkpoint_and_readback_fail_without_any_write() -> None:
    document = _native_table((("Read only",), ("Read and write",)))
    document["revisionId"] = "r1"
    _white_text(document, inherited=False)
    fixture = Path(__file__).parent / "data" / "readability" / "legacy-contrast-checkpoint.json"
    plan = PlannedReadabilityCheckpoint.model_validate_json(fixture.read_text())
    assert plan.source_sha256 == document_digest(document)
    assert len(plan.tables) == 1
    after = _body_readback(document, plan.tables[0])
    _white_text(after, inherited=False)
    table = obj(obj(content(next(bodies(after))[1])[0])["table"])
    for index, row in enumerate(array(table["tableRows"])):
        for value in array(obj(row)["tableCells"]):
            shade = 245 / 255 if index % 2 == 0 else 1
            obj(obj(value)["tableCellStyle"])["backgroundColor"] = {
                "color": {"rgbColor": dict.fromkeys(("red", "green", "blue"), shade)}
            }

    original = deepcopy(document)
    assert not verify_readability(after, plan)
    with pytest.raises(ValueError, match="Table shading would make text unreadable"):
        readable_table_requests(document, plan.tables)

    async def read(_document_id: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def write(_document_id: FileId, _checkpoint: ReadabilityCheckpoint) -> None:
        pytest.fail("Unsafe legacy shading must fail before any native write")

    with pytest.raises(OutputConflictError):
        await ReadabilityProvider(read, write).apply(
            document_id=FileId("contrast"), checkpoint=plan
        )
    assert document == original
