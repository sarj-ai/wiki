from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.errors import InPlaceNeedsReviewError, SourceChangedError
from app.document_transform.native_chip_flow import (
    CompletedNativeChipCheckpoint,
    NativeChipCheckpoint,
    NativeChipProvider,
    has_chip_marker,
    plan_native_chips,
    verify_native_chips,
)
from app.document_transform.readability_json import array, bodies, obj
from app.identifiers import FileId
from tests.test_native_table_font import _document as table_document


def _fixture(name: str) -> dict[str, JsonValue]:
    return json.loads(
        (Path(__file__).parent / "data" / "native_chips" / f"{name}.json").read_text()
    )


def _checkpoint() -> NativeChipCheckpoint:
    checkpoint = plan_native_chips(_fixture("before"), operation_id=uuid.UUID(int=1))
    assert checkpoint is not None
    return checkpoint.model_copy(update={"marker_name": "sarj_native_date_range_canary_20261112"})


def _elements(document: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    return [
        obj(part)
        for _, tab in bodies(document)
        for value in array(obj(tab["body"])["content"])
        if "paragraph" in (node := obj(value))
        for part in array(obj(node["paragraph"])["elements"])
    ]


def test_captured_native_google_date_range_passes_with_surrounding_content_preserved() -> None:
    checkpoint = _checkpoint()
    assert len(checkpoint.spans) == 2
    assert [span.start for span in checkpoint.spans] == [121, 97]
    assert len([skip for skip in checkpoint.skipped if skip.kind == "place"]) == 2
    after = _fixture("after")
    assert has_chip_marker(after, checkpoint)
    verify_native_chips(after, checkpoint)


def test_captured_date_only_overnight_and_emoji_chips_preserve_native_semantics_and_punctuation() -> (
    None
):
    checkpoint = plan_native_chips(
        _fixture("semantics_before"), operation_id=uuid.UUID("550e8400-e29b-41d4-a716-446655442020")
    )
    assert checkpoint is not None
    assert len(checkpoint.spans) == 4
    assert sum(not span.date_value.has_time for span in checkpoint.spans) == 1
    after = _fixture("semantics_after")
    assert has_chip_marker(after, checkpoint)
    verify_native_chips(after, checkpoint)
    repeated = plan_native_chips(after, operation_id=uuid.uuid7())
    assert repeated is not None
    assert not repeated.spans


def test_captured_table_date_conversion_preserves_effective_newline_style() -> None:
    checkpoint = plan_native_chips(_fixture("table_style_before"), operation_id=uuid.UUID(int=2))
    assert checkpoint is not None
    assert len(checkpoint.spans) == 4
    after = _fixture("table_style_after")
    assert has_chip_marker(after, checkpoint)
    verify_native_chips(after, checkpoint)


@pytest.mark.parametrize(
    "corruption",
    ["font", "bold", "link", "unknown-style", "annotation", "parent-style", "other-paragraph"],
)
def test_table_date_equivalence_still_rejects_native_preservation_changes(corruption: str) -> None:
    checkpoint = plan_native_chips(_fixture("table_style_before"), operation_id=uuid.UUID(int=2))
    assert checkpoint is not None
    after = _fixture("table_style_after")
    _, tab = next(bodies(after))
    content = array(obj(tab["body"])["content"])
    table = obj(obj(content[10])["table"])
    row = obj(array(table["tableRows"])[2])
    cell = obj(array(row["tableCells"])[2])
    paragraph = obj(obj(array(cell["content"])[0])["paragraph"])
    newline = obj(array(paragraph["elements"])[-1])
    style = obj(obj(newline["textRun"])["textStyle"])
    match corruption:
        case "font":
            style["fontSize"] = {"magnitude": 11, "unit": "PT"}
        case "bold":
            style["bold"] = True
        case "link":
            style["link"] = {"url": "https://example.test/changed"}
        case "unknown-style":
            style["futureDecoration"] = "changed"
        case "annotation":
            newline["suggestedInsertionIds"] = ["new-suggestion"]
        case "parent-style":
            named = obj(tab["namedStyles"])
            normal = next(
                obj(item)
                for item in array(named["styles"])
                if obj(item)["namedStyleType"] == "NORMAL_TEXT"
            )
            obj(normal["textStyle"])["bold"] = True
        case _:
            untouched = obj(obj(content[1])["paragraph"])
            run = obj(obj(array(untouched["elements"])[0])["textRun"])
            obj(run["textStyle"])["futureDecoration"] = "changed"
    with pytest.raises(InPlaceNeedsReviewError):
        verify_native_chips(after, checkpoint)


@pytest.mark.parametrize(
    "corruption", ["date", "zone", "date-type", "existing-chip", "text", "marker", "link", "style"]
)
def test_native_projection_rejects_meaningful_changes_beyond_the_planned_conversion(
    corruption: str,
) -> None:
    after = _fixture("after")
    elements = _elements(after)
    dates = [obj(part["dateElement"]) for part in elements if "dateElement" in part]
    plain = next(obj(part["textRun"]) for part in elements if "textRun" in part)
    match corruption:
        case "date":
            obj(dates[1]["dateElementProperties"])["timestamp"] = "2026-11-16T00:30:00Z"
        case "zone":
            obj(dates[1]["dateElementProperties"])["timeZoneId"] = "UTC"
        case "date-type":
            part = next(part for part in elements if part.get("startIndex") == 97)
            part.pop("dateElement")
            part["textRun"] = {"content": "x", "textStyle": {}}
        case "existing-chip":
            obj(dates[0]["dateElementProperties"])["timestamp"] = "2026-11-16T00:30:00Z"
        case "text":
            plain["content"] = str(plain["content"]).replace("NYC", "NXC")
        case "marker":
            _, tab = next(bodies(after))
            tab.pop("namedRanges")
        case "link":
            plain["textStyle"] = {"link": {"url": "https://example.com/changed"}}
        case _:
            dates[1]["textStyle"] = {"bold": True}
    with pytest.raises(InPlaceNeedsReviewError):
        verify_native_chips(after, _checkpoint())


async def test_interrupted_write_is_recovered_from_native_marker_without_repeating_mutation() -> (
    None
):
    state = _fixture("before")
    writes = 0

    async def read(_: FileId) -> dict[str, JsonValue]:
        return deepcopy(state)

    async def write(_: FileId, __: NativeChipCheckpoint) -> None:
        nonlocal state, writes
        writes += 1
        state = _fixture("after")
        raise TimeoutError

    provider = NativeChipProvider(read=read, write=write)
    checkpoint = _checkpoint()
    with pytest.raises(TimeoutError):
        await provider.apply(checkpoint)
    completed = await provider.apply(checkpoint)
    assert isinstance(completed, CompletedNativeChipCheckpoint)
    assert writes == 1
    assert await provider.apply(completed) == completed
    assert writes == 1


async def test_revision_conflict_does_not_write_or_rebase_the_conversion() -> None:
    state = _fixture("before")
    state["revisionId"] = "concurrent-editor"

    async def read(_: FileId) -> dict[str, JsonValue]:
        return state

    async def forbidden_write(_: FileId, __: NativeChipCheckpoint) -> None:
        pytest.fail("a changed source must not be mutated")

    with pytest.raises(SourceChangedError):
        await NativeChipProvider(read=read, write=forbidden_write).apply(_checkpoint())


@pytest.mark.parametrize("corruption", ["requests", "meaning", "range"])
async def test_corrupt_saved_chip_intent_is_rejected_before_any_mutation(corruption: str) -> None:
    checkpoint = _checkpoint()
    match corruption:
        case "requests":
            checkpoint = checkpoint.model_copy(
                update={
                    "requests": [
                        {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": 10}}}
                    ]
                }
            )
        case "meaning":
            first, *rest = checkpoint.spans
            changed = first.model_copy(
                update={"date_value": replace(first.date_value, timestamp="2026-11-16T00:30:00Z")}
            )
            checkpoint = checkpoint.model_copy(update={"spans": (changed, *rest)})
        case _:
            checkpoint = checkpoint.model_copy(
                update={"marker_range": {"tabId": "t.0", "startIndex": 1, "endIndex": 2}}
            )

    async def read(_: FileId) -> dict[str, JsonValue]:
        return _fixture("before")

    async def forbidden_write(_: FileId, __: NativeChipCheckpoint) -> None:
        pytest.fail("corrupt persisted intent must not reach Google mutation")

    with pytest.raises(InPlaceNeedsReviewError):
        await NativeChipProvider(read=read, write=forbidden_write).apply(checkpoint)


@pytest.mark.parametrize("layout", ["bullet", "table", "repeated-table"])
async def test_53_native_address_spans_in_bullets_and_tables_are_reported_without_substitutes(
    layout: str,
) -> None:
    text = "533 W. 19th St. and 537 W. 20th St. Exhibition"
    if layout == "bullet":
        document = _fixture("before")
        _, tab = next(bodies(document))
        content = array(obj(tab["body"])["content"])
        obj(tab["body"])["content"] = content[:3]
        obj(obj(content[2])["paragraph"])["bullet"] = {"listId": "addresses", "nestingLevel": 0}
        tab["lists"] = {"addresses": {"listProperties": {"nestingLevels": [{"glyphSymbol": "●"}]}}}
    else:
        document = table_document(text if layout == "table" else f"{text} and {text}")
        document["revisionId"] = "table-source-r1"
        document["title"] = "NYC galleries 2026"
    checkpoint = plan_native_chips(document, operation_id=uuid.uuid7())
    assert checkpoint is not None
    places = [item for item in checkpoint.skipped if item.kind == "place"]
    assert len(places) == (4 if layout == "repeated-table" else 2)
    assert all(
        item.reason == "native_place_insertion_unsupported_by_verified_api" for item in places
    )
    assert not checkpoint.spans
    assert not checkpoint.requests
    before = deepcopy(document)

    async def read(_: FileId) -> dict[str, JsonValue]:
        return deepcopy(document)

    async def forbidden_write(_: FileId, __: NativeChipCheckpoint) -> None:
        pytest.fail("unsupported places must not be replaced with links or flattened text")

    assert (await NativeChipProvider(read, forbidden_write).apply(checkpoint)).complete
    assert document == before
