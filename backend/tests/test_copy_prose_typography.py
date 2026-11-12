from copy import deepcopy
from dataclasses import replace

from pydantic import JsonValue
import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.copy_font_defense import (
    copy_preserved_font_ids,
    defend_copy_fonts,
    verify_copy_fonts,
)
from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_font_defense import apply_native_font_preservation
from app.document_transform.readability_json import array, bodies, content, integer, obj


def _document(*, leading_link: bool, intro: bool = False) -> dict[str, JsonValue]:
    cursor = 1
    paragraphs: list[JsonValue] = []
    runs: list[tuple[str, dict[str, JsonValue]]] = [
        ("Reference", {"link": {"url": "https://example.test/source"}, "underline": True}),
        (" retains the source constraints.\n", {"italic": True}),
    ]
    if not leading_link:
        runs.insert(0, ("It is important to note that the ", {"bold": True}))
    groups = ([[("Welcome 🙂\n", {})]] if intro else []) + [runs]
    groups.append(
        [
            (
                "keep  code\n",
                {"weightedFontFamily": {"fontFamily": "Courier New", "weight": 400}, "bold": True},
            )
        ]
    )
    for group in groups:
        start = cursor
        elements: list[JsonValue] = []
        for text, style in group:
            end = cursor + utf16_length(text)
            elements.append(
                {
                    "startIndex": cursor,
                    "endIndex": end,
                    "textRun": {"content": text, "textStyle": style},
                }
            )
            cursor = end
        paragraphs.append(
            {
                "startIndex": start,
                "endIndex": cursor,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": elements,
                },
            }
        )
    return {
        "revisionId": "source",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab"},
                "documentTab": {
                    "body": {"content": paragraphs},
                    "namedStyles": {
                        "styles": [
                            {
                                "namedStyleType": "NORMAL_TEXT",
                                "textStyle": {
                                    "bold": True,
                                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 700},
                                },
                            }
                        ]
                    },
                },
            }
        ],
    }


def _body(document: dict[str, JsonValue]) -> list[JsonValue]:
    return content(next(bodies(document))[1])


def _regular_output(source: dict[str, JsonValue], *, intro: bool = False) -> dict[str, JsonValue]:
    actual = deepcopy(source)
    paragraph = obj(obj(_body(actual)[int(intro)])["paragraph"])
    obj(paragraph["paragraphStyle"])["alignment"] = "JUSTIFIED"
    parts: list[JsonValue] = []
    for value in array(paragraph["elements"]):
        part = obj(value)
        run = obj(part["textRun"])
        text = str(run["content"])
        terminal = deepcopy(part) if text.endswith("\n") else None
        if terminal is not None:
            part["endIndex"] = integer(part["endIndex"]) - 1
            run["content"] = text[:-1]
            terminal["startIndex"] = part["endIndex"]
            obj(terminal["textRun"])["content"] = "\n"
        obj(run["textStyle"]).update(
            {"bold": False, "weightedFontFamily": {"fontFamily": "Nunito", "weight": 400}}
        )
        parts.append(part)
        if terminal is not None:
            parts.append(terminal)
    paragraph["elements"] = parts
    return actual


@pytest.mark.parametrize("leading_link", [False, True])
def test_copy_normalizes_inherited_body_weight_and_preserves_link_italic_and_code(
    *, leading_link: bool
):
    source = _document(leading_link=leading_link)
    original = replace(parse_google_document(source, title="Report"), copy_font_defended=True)
    expected = apply_native_font_preservation(
        original, copy_preserved_font_ids(source, original, original)
    )
    actual = _regular_output(source)
    assert verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)
    assert not verify_copy_fonts(source, source, expected, list_identity_equivalent=False)
    requests = defend_copy_fonts(
        build_copy_mutations(
            original, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
        ),
        document=source,
        original=original,
        transformed=expected,
        font_family="Nunito",
    )
    regular = [
        r["updateTextStyle"]
        for r in requests
        if r.get("updateTextStyle", {}).get("textStyle", {}).get("bold") is False
    ]
    assert regular
    assert all(
        r["textStyle"]["weightedFontFamily"] == {"fontFamily": "Nunito", "weight": 400}
        for r in regular
    )
    assert _body(actual)[-1] == _body(source)[-1]


@pytest.mark.parametrize("leading_link", [False, True])
@pytest.mark.parametrize("change", ["bold", "weight", "link"])
def test_regular_body_readback_rejects_failed_formatting_and_changed_citations(
    *, leading_link: bool, change: str
):
    source = _document(leading_link=leading_link)
    original = replace(parse_google_document(source, title="Report"), copy_font_defended=True)
    expected = apply_native_font_preservation(
        original, copy_preserved_font_ids(source, original, original)
    )
    actual = _regular_output(source)
    paragraph = obj(obj(_body(actual)[0])["paragraph"])
    parts = [obj(value) for value in array(paragraph["elements"])]
    style = obj(obj(parts[0]["textRun"])["textStyle"])
    match change:
        case "bold":
            style["bold"] = True
        case "weight":
            obj(style["weightedFontFamily"])["weight"] = 700
        case _:
            linked = next(
                obj(obj(p["textRun"])["textStyle"])
                for p in parts
                if "link" in obj(obj(p["textRun"])["textStyle"])
            )
            obj(linked["link"])["url"] = "https://example.test/changed"
    assert not verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)


def test_short_native_citation_prefix_can_be_unslopped_without_unlocking_its_link():
    document = parse_google_document(_document(leading_link=False), title="Report")
    block = document.blocks[0]
    assert block.editable
    assert block.protected_suffix == "Reference retains the source constraints."


def test_copy_prose_font_and_citation_proofs_follow_shortened_prefixes_and_utf16_deletions():
    source = _document(leading_link=False, intro=True)
    original = replace(parse_google_document(source, title="Report"), copy_font_defended=True)
    body = original.blocks[1]
    assert body.protected_suffix is not None
    changed = original.replace_blocks(
        {original.blocks[0].id: "", body.id: "The " + body.protected_suffix}
    )
    expected = apply_native_font_preservation(
        changed, copy_preserved_font_ids(source, original, changed)
    )
    actual = _regular_output(source, intro=True)
    paragraphs = [obj(value) for value in _body(actual)]
    paragraphs[0]["endIndex"] = 2
    obj(paragraphs[0]["paragraph"])["elements"] = [
        {"startIndex": 1, "endIndex": 2, "textRun": {"content": "\n", "textStyle": {}}}
    ]
    body_parts = array(obj(paragraphs[1]["paragraph"])["elements"])
    obj(obj(body_parts[0])["textRun"])["content"] = "The "
    cursor = 2
    for paragraph in paragraphs[1:]:
        paragraph["startIndex"] = cursor
        for value in array(obj(paragraph["paragraph"])["elements"]):
            part = obj(value)
            part["startIndex"] = cursor
            cursor += utf16_length(str(obj(part["textRun"])["content"]))
            part["endIndex"] = cursor
        paragraph["endIndex"] = cursor
    assert obj(body_parts[1])["startIndex"] == 6
    assert verify_copy_fonts(source, actual, expected, list_identity_equivalent=False)
