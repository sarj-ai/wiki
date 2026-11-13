from copy import deepcopy
from typing import Literal
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.errors import InPlaceNeedsReviewError
from app.document_transform.ir import utf16_length
from app.document_transform.native_chip_flow import (
    NativeChipCheckpoint,
    NativeChipProvider,
    legacy_protected_conversion_count,
    plan_native_chips,
)
from app.document_transform.readability_json import array, bodies, integer, obj
from app.identifiers import FileId
from tests.test_native_chip_flow import _fixture
from tests.test_native_table_font import _document as table_document


@pytest.mark.parametrize("layout", ["paragraph", "bullet", "table"])
def test_inline_native_code_stays_literal_and_prose_dates_keep_utf16_positions(
    layout: Literal["paragraph", "bullet", "table"],
) -> None:
    document = _document("November 14, 2026", "; visit November 15, 2026.\n", layout=layout)
    before = deepcopy(document)
    checkpoint = plan_native_chips(document, operation_id=uuid.UUID(int=1))
    assert checkpoint is not None
    assert [span.original_text for span in checkpoint.spans] == ["November 15, 2026"]
    assert any(item.reason == "protected_code" for item in checkpoint.skipped)
    start = 103 if layout == "table" else 1
    assert checkpoint.spans[0].start == start + utf16_length("🗽 Code: November 14, 2026; visit ")
    assert document == before


def test_native_code_date_cannot_anchor_a_prose_time_range() -> None:
    checkpoint = plan_native_chips(
        _document("November 14, 2026", ", 7:30–9:00 pm.\n", layout="paragraph"),
        operation_id=uuid.UUID(int=1),
    )
    assert checkpoint is not None
    assert not checkpoint.spans
    assert any(item.reason == "protected_code_context" for item in checkpoint.skipped)


def test_native_code_year_does_not_supply_missing_prose_date_context() -> None:
    document = _document("November 14, 2026", "; visit December 15.\n", layout="paragraph")
    document["title"] = "NYC examples"
    checkpoint = plan_native_chips(document, operation_id=uuid.UUID(int=1))
    assert checkpoint is not None
    assert not checkpoint.spans


def test_inherited_code_font_preserves_code_but_respects_explicit_prose_font() -> None:
    document = _document("November 14, 2026", "; visit November 15, 2026.\n", layout="paragraph")
    _, tab = next(bodies(document))
    normal = obj(array(obj(tab["namedStyles"])["styles"])[0])
    obj(obj(normal["textStyle"])["weightedFontFamily"])["fontFamily"] = "Consolas"
    paragraph = obj(obj(array(obj(tab["body"])["content"])[0])["paragraph"])
    elements = array(paragraph["elements"])
    for index, value in enumerate(elements):
        obj(obj(value)["textRun"])["textStyle"] = (
            {} if index == 1 else {"weightedFontFamily": {"fontFamily": "Arial", "weight": 400}}
        )
    checkpoint = plan_native_chips(document, operation_id=uuid.UUID(int=1))
    assert checkpoint is not None
    assert [span.original_text for span in checkpoint.spans] == ["November 15, 2026"]
    assert any(item.reason == "protected_code" for item in checkpoint.skipped)


async def test_unsafe_legacy_inline_code_plan_is_stopped_before_google_write() -> None:
    document = _document("November 14, 2026", "; visit November 15, 2026.\n", layout="paragraph")
    legacy = plan_native_chips(document, operation_id=uuid.UUID(int=1), planner_version=2)
    assert legacy is not None
    assert len(legacy.spans) == 2

    async def read(_: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def forbidden_write(_: FileId, __: NativeChipCheckpoint) -> None:
        pytest.fail("A persisted unsafe code conversion must not reach Google")

    with pytest.raises(InPlaceNeedsReviewError):
        await NativeChipProvider(read, forbidden_write).apply(legacy)


def test_safe_legacy_inline_code_plan_keeps_its_exact_conversion_intent() -> None:
    document = _document("counter = 23", "; visit November 15, 2026.\n", layout="paragraph")
    legacy = plan_native_chips(document, operation_id=uuid.UUID(int=1), planner_version=2)
    assert legacy is not None
    assert len(legacy.spans) == 1
    assert legacy_protected_conversion_count(legacy) == 0
    current = plan_native_chips(document, operation_id=uuid.UUID(int=1))
    assert current is not None
    assert current.spans == legacy.spans
    assert current.requests == legacy.requests


def _document(
    code: str, suffix: str, *, layout: Literal["paragraph", "bullet", "table"]
) -> dict[str, JsonValue]:
    prefix = "🗽 Code: "
    source = _fixture("before")
    document = (
        table_document(f"{prefix}{code}{suffix.removesuffix('\n')}")
        if layout == "table"
        else source
    )
    document.update({"documentId": "source", "revisionId": "r1", "title": "NYC examples 2026"})
    _, tab = next(bodies(document))
    tab["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": {
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                    "bold": False,
                },
            }
        ]
    }
    tab.pop("namedRanges", None)
    node: dict[str, JsonValue]
    if layout == "table":
        table = obj(obj(array(obj(tab["body"])["content"])[-1])["table"])
        row = obj(array(table["tableRows"])[0])
        cell = obj(array(row["tableCells"])[0])
        node = obj(array(cell["content"])[0])
    else:
        node = {"startIndex": 1, "endIndex": 1 + utf16_length(f"{prefix}{code}{suffix}")}
        obj(tab["body"])["content"] = [node]
    cursor = integer(node["startIndex"])
    parts: list[JsonValue] = []
    runs: tuple[tuple[str, dict[str, JsonValue]], ...] = (
        (prefix, {}),
        (code, {"weightedFontFamily": {"fontFamily": "Consolas", "weight": 400}}),
        (suffix, {}),
    )
    for text, style in runs:
        end = cursor + utf16_length(text)
        parts.append(
            {
                "startIndex": cursor,
                "endIndex": end,
                "textRun": {"content": text, "textStyle": style},
            }
        )
        cursor = end
    node["paragraph"] = {"elements": parts, "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}
    if layout == "bullet":
        obj(node["paragraph"])["bullet"] = {"listId": "list", "nestingLevel": 0}
        tab["lists"] = {"list": {"listProperties": {"nestingLevels": [{"glyphSymbol": "●"}]}}}
    return document
