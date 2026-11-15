from io import BytesIO
from typing import NamedTuple

from docx import Document
from pydantic import JsonValue
import pytest

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.docx_renderer import markdown_import_manifest, render_markdown_docx
from app.document_transform.import_manifest import MarkdownImportManifest
from app.document_transform.markdown_import import verify_markdown_import
from app.document_transform.verification import BrandVerification
from tests.test_markdown_import import _brand_run_style, _branded_document, _paragraph


def test_renderer_styles_empty_headers_and_body_cells_without_inserting_text() -> None:
    document = Document(
        BytesIO(
            render_markdown_docx("| | Observed |\n| --- | --- |\n| Q-001 | |", title="Empty cells")
        )
    )
    header = document.tables[0].cell(0, 0).paragraphs[0]
    body = document.tables[0].cell(1, 1).paragraphs[0]

    assert not header.text
    assert not body.text
    assert header.style is not None
    assert header.style.font.bold is True
    assert body.style is not None
    assert body.style.font.bold is False


class _EmptyCellFixture(NamedTuple):
    document: dict[str, JsonValue]
    elements: list[dict[str, JsonValue]]
    text_run: dict[str, JsonValue]
    text_style: dict[str, JsonValue]
    paragraph_style: dict[str, JsonValue]


def _empty_cell_document(
    *, empty_header: bool = False, inherited: bool = False
) -> _EmptyCellFixture:
    document = _branded_document()
    tab = document["tabs"][0]["documentTab"]
    rows = [["Case", "" if empty_header else "Observed", "Expected"], ["Q-001", "", "0"]]
    table_rows = []
    for index, values in enumerate(rows):
        cells = []
        for value in values:
            style = _brand_run_style(
                size=10, color=CURRENT_BRAND_MANIFEST.colors.ink, bold=index == 0
            )
            if inherited and not value:
                style = {"bold": index == 0}
            cells.append(
                {
                    "content": [
                        _paragraph(
                            [(value + "\n", style)],
                            style={
                                "namedStyleType": "NORMAL_TEXT",
                                "alignment": "START",
                                "lineSpacing": 100,
                                "spaceAbove": {"magnitude": 0, "unit": "PT"},
                                "spaceBelow": {"magnitude": 2, "unit": "PT"},
                            },
                        )
                    ]
                }
            )
        table_rows.append({"tableCells": cells})
    tab["body"]["content"].append({"table": {"tableRows": table_rows}})
    if inherited:
        tab["namedStyles"] = {
            "styles": [
                {
                    "namedStyleType": "NORMAL_TEXT",
                    "textStyle": _brand_run_style(size=10, color=CURRENT_BRAND_MANIFEST.colors.ink),
                }
            ]
        }
        tab["footers"]["footer-1"]["content"][0]["paragraph"]["elements"][0]["textRun"][
            "textStyle"
        ] = _brand_run_style(size=9, color=CURRENT_BRAND_MANIFEST.colors.muted)
    paragraph = table_rows[1]["tableCells"][1]["content"][0]["paragraph"]
    return _EmptyCellFixture(
        document,
        paragraph["elements"],
        paragraph["elements"][0]["textRun"],
        paragraph["elements"][0]["textRun"]["textStyle"],
        paragraph["paragraphStyle"],
    )


def _manifest(*, empty_header: bool = False) -> MarkdownImportManifest:
    heading = "" if empty_header else "Observed"
    return markdown_import_manifest(
        f"Body\n\n| Case | {heading} | Expected |\n| --- | --- | --- |\n| Q-001 | | 0 |",
        title="Import",
        branded=True,
    )


@pytest.mark.parametrize("empty_header", [False, True])
@pytest.mark.parametrize("inherited", [False, True])
def test_styled_empty_cells_preserve_content_structure_and_brand(
    *, empty_header: bool, inherited: bool
) -> None:
    result = verify_markdown_import(
        _empty_cell_document(empty_header=empty_header, inherited=inherited).document,
        _manifest(empty_header=empty_header),
    )

    assert result.content_preserved
    assert result.structure_preserved
    assert result.brand is BrandVerification.FULL


@pytest.mark.parametrize("corruption", ["font", "zero-size", "bold", "spacing", "link"])
def test_empty_cell_still_requires_the_actual_paragraph_break_style(corruption: str) -> None:
    fixture = _empty_cell_document(inherited=True)
    style = fixture.text_style
    match corruption:
        case "font":
            style["weightedFontFamily"] = {"fontFamily": "Times New Roman"}
        case "zero-size":
            style["fontSize"] = {"magnitude": 0, "unit": "PT"}
        case "bold":
            style["bold"] = True
        case "spacing":
            fixture.paragraph_style["spaceBelow"] = {"magnitude": 20, "unit": "PT"}
        case _:
            style["link"] = {"url": "https://example.com/unplanned"}

    result = verify_markdown_import(fixture.document, _manifest())

    assert result.content_preserved
    assert result.brand not in {BrandVerification.FULL, BrandVerification.NOT_REQUIRED}


@pytest.mark.parametrize("unexpected", ["zero", "image"])
def test_empty_cell_does_not_accept_a_zero_or_an_unplanned_native_object(unexpected: str) -> None:
    fixture = _empty_cell_document()
    elements = fixture.elements
    if unexpected == "zero":
        fixture.text_run["content"] = "0\n"
    else:
        elements.insert(0, {"inlineObjectElement": {"inlineObjectId": "unplanned"}})

    result = verify_markdown_import(fixture.document, _manifest())

    assert result.brand not in {BrandVerification.FULL, BrandVerification.NOT_REQUIRED}
    assert not result.content_preserved or not result.structure_preserved
