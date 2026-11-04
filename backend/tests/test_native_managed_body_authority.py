from copy import deepcopy
from dataclasses import replace
import uuid

from pydantic import JsonValue
import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_font_context import native_normal_text_style
from app.document_transform.native_visual_authority import visual_authority_block_ids
from app.document_transform.schemas import OutputStrategy, TransformMode
from app.identifiers import TaskId
from tests.test_document_transform import (
    _copy_executor,
    _CopyCheckpointStore,
    _Gateway,
    _in_place_request,
)
from tests.test_document_transform_header_provenance import _snapshot
from tests.test_native_visual_authority import _document, _paragraph


_SOURCE = "Moreover, the team approved the launch after reviewing the complete plan."
_TIGHTENED = "The team approved the launch after reviewing the complete plan."


def _apply_body_formatting(document: dict[str, JsonValue]) -> None:
    tabs = document["tabs"]
    assert isinstance(tabs, list)
    tab = tabs[0]
    assert isinstance(tab, dict)
    native = tab["documentTab"]
    assert isinstance(native, dict)
    body = native["body"]
    assert isinstance(body, dict)
    content = body["content"]
    assert isinstance(content, list)
    element = content[0]
    assert isinstance(element, dict)
    normal = native_normal_text_style(document, "t.0")
    assert normal is not None
    normal["weightedFontFamily"] = {"fontFamily": "Arial"}
    paragraph = _paragraph(document)
    paragraph["paragraphStyle"]["alignment"] = "START"
    end = 1 + utf16_length(_SOURCE)
    paragraph["elements"] = [
        {
            "startIndex": 1,
            "endIndex": end,
            "textRun": {"content": _SOURCE, "textStyle": deepcopy(normal)},
        },
        {
            "startIndex": end,
            "endIndex": end + 1,
            "textRun": {"content": "\n", "textStyle": deepcopy(normal)},
        },
    ]
    element["endIndex"] = end + 1
    original = parse_google_document(document, title="Source")
    for request in build_copy_mutations(
        original,
        original,
        profile=SarjBrandProfile(),
        headers=(),
        logo_uri=CURRENT_BRAND_MANIFEST.assets.logo_uri,
    ):
        if "updateParagraphStyle" in request:
            paragraph["paragraphStyle"].update(request["updateParagraphStyle"]["paragraphStyle"])
        if "updateTextStyle" in request:
            mutation = request["updateTextStyle"]
            for element in paragraph["elements"]:
                if (
                    element["startIndex"] >= mutation["range"]["startIndex"]
                    and element["endIndex"] <= mutation["range"]["endIndex"]
                ):
                    run = element["textRun"]
                    assert isinstance(run, dict)
                    style = run["textStyle"]
                    assert isinstance(style, dict)
                    style.update(mutation["textStyle"])


async def test_format_output_can_be_tightened_by_subsequent_both_copy() -> None:
    document = _document(12)
    _apply_body_formatting(document)
    native = document["tabs"][0]["documentTab"]
    assert native["namedStyles"]["styles"][0]["textStyle"]["weightedFontFamily"] == {
        "fontFamily": "Arial"
    }
    original = parse_google_document(document, title="Source")
    assert original.blocks[0].editable
    assert visual_authority_block_ids(document, original) == ()
    snapshot = replace(_snapshot(document), modified_time="2026-09-03T00:00:00Z")
    gateway, store, operation = _Gateway(snapshot), _CopyCheckpointStore(), TaskId(uuid.uuid7())
    request = _in_place_request(TransformMode.BOTH).model_copy(
        update={"output_strategy": OutputStrategy.COPY}
    )
    result = await _copy_executor(gateway=gateway, store=store, task_id=operation).execute(
        operation_id=operation, request=request
    )
    assert store.checkpoints[-1].replacements == {"b0": _TIGHTENED}
    assert result.applied_patch_count == 1
    assert not any(
        finding.code == "native_visual_authority_preserved" for finding in result.findings
    )


def test_current_body_size_can_be_inherited_and_google_rgb_can_be_float32() -> None:
    document = _document(12)
    _apply_body_formatting(document)
    native = document["tabs"][0]["documentTab"]
    native["namedStyles"]["styles"][0]["textStyle"]["fontSize"] = {
        "magnitude": CURRENT_BRAND_MANIFEST.document.body_size_pt,
        "unit": "PT",
    }
    style = _paragraph(document)["elements"][0]["textRun"]["textStyle"]
    style.pop("fontSize")
    style["foregroundColor"]["color"]["rgbColor"] = {
        channel: round(value, 9)
        for channel, value in style["foregroundColor"]["color"]["rgbColor"].items()
    }
    original = parse_google_document(document, title="Source")
    assert original.blocks[0].editable
    assert visual_authority_block_ids(document, original) == ()


def test_explicit_ordinary_effects_remain_body_style() -> None:
    document = _document(12)
    _apply_body_formatting(document)
    style = _paragraph(document)["elements"][0]["textRun"]["textStyle"]
    style["weightedFontFamily"]["weight"] = 400
    style.update(
        bold=False,
        italic=False,
        underline=False,
        strikethrough=False,
        smallCaps=False,
        baselineOffset="NONE",
        backgroundColor={},
    )
    original = parse_google_document(document, title="Source")
    assert original.blocks[0].editable
    assert visual_authority_block_ids(document, original) == ()


@pytest.mark.parametrize(
    "change",
    [
        "size",
        "color",
        "family",
        "missing_family",
        "weight",
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "background",
        "alignment",
        "unknown_effect",
        "inherited_bold",
        "inherited_unknown_effect",
        "unknown_normal_context",
    ],
)
def test_custom_presentation_is_not_mistaken_for_managed_body(change: str) -> None:
    document = _document(12)
    _apply_body_formatting(document)
    native = document["tabs"][0]["documentTab"]
    paragraph = _paragraph(document)
    style = paragraph["elements"][0]["textRun"]["textStyle"]
    match change:
        case "size":
            style["fontSize"]["magnitude"] += 1
        case "color":
            style["foregroundColor"]["color"]["rgbColor"]["red"] += 1 / 255
        case "family":
            style["weightedFontFamily"]["fontFamily"] = "Georgia"
        case "missing_family":
            style.pop("weightedFontFamily")
        case "weight":
            style["weightedFontFamily"]["weight"] = 700
        case "bold" | "italic" | "underline" | "strikethrough":
            style[change] = True
        case "background":
            style["backgroundColor"] = {"color": {"rgbColor": {"red": 1}}}
        case "alignment":
            paragraph["paragraphStyle"]["alignment"] = "CENTER"
        case "unknown_effect":
            style["futureEffect"] = True
        case "inherited_bold":
            native["namedStyles"]["styles"][0]["textStyle"]["bold"] = True
        case "inherited_unknown_effect":
            native["namedStyles"]["styles"][0]["textStyle"]["futureEffect"] = True
        case "unknown_normal_context":
            document["namedStyles"] = deepcopy(native["namedStyles"])
            document["namedStyles"]["styles"][0]["textStyle"]["fontSize"]["magnitude"] += 1
        case _:
            raise AssertionError(change)
    original = parse_google_document(document, title="Source")
    assert original.blocks[0].editable
    assert visual_authority_block_ids(document, original) == ("b0",)


def test_superscript_remains_noneditable() -> None:
    document = _document(12)
    _apply_body_formatting(document)
    _paragraph(document)["elements"][0]["textRun"]["textStyle"]["baselineOffset"] = "SUPERSCRIPT"
    original = parse_google_document(document, title="Source")
    assert not original.blocks[0].editable
    assert visual_authority_block_ids(document, original) == ()


def test_distinct_visible_rich_text_remains_noneditable() -> None:
    document = _document(12)
    _apply_body_formatting(document)
    paragraph = _paragraph(document)
    first = paragraph["elements"][0]
    remainder = deepcopy(first)
    split = _SOURCE.index("team")
    first["endIndex"] = 1 + split
    first["textRun"]["content"] = _SOURCE[:split]
    remainder["startIndex"] = first["endIndex"]
    remainder["textRun"]["content"] = _SOURCE[split:]
    remainder["textRun"]["textStyle"]["bold"] = True
    paragraph["elements"].insert(1, remainder)
    original = parse_google_document(document, title="Source")
    assert not original.blocks[0].editable
    assert visual_authority_block_ids(document, original) == ()
