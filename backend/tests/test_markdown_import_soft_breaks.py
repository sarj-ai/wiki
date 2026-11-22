from copy import deepcopy
from pathlib import Path

from pydantic import JsonValue, RootModel
import pytest

from app.document_transform.docx_renderer import markdown_import_manifest
from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.markdown_import import verify_markdown_import
from app.document_transform.readability_json import array, obj


_SOURCE = 'QA example:\n\n```python\ndef test():\n\treturn "a  b"\n```\n'
_FIXTURE = Path(__file__).parent / "data" / "markdown_code_soft_breaks.json"


class _NativeFixture(RootModel[dict[str, JsonValue]]):
    pass


def _document() -> dict[str, JsonValue]:
    return _NativeFixture.model_validate_json(_FIXTURE.read_text()).root


def test_actual_native_code_soft_breaks_and_bottom_border_verify_without_changing_the_snapshot():
    document = _document()
    original = deepcopy(document)
    manifest = markdown_import_manifest(_SOURCE, title="r01-python", branded=False)

    result = verify_markdown_import(document, manifest)

    assert result.content_preserved
    assert result.structure_preserved
    assert document == original


@pytest.mark.parametrize(
    ("before", "after"),
    [("\t", "    "), ("a  b", "a b"), ("return", "raise"), ("\v", " ")],
)
def test_soft_break_equivalence_cannot_hide_code_or_whitespace_loss(before: str, after: str):
    document = _document()
    tab = obj(array(document["tabs"])[0])
    for element in array(obj(obj(tab["documentTab"])["body"])["content"]):
        for run in array(obj(obj(element)["paragraph"])["elements"]):
            text = obj(obj(run)["textRun"])
            text["content"] = str(text["content"]).replace(before, after)
    manifest = markdown_import_manifest(_SOURCE, title="r01-python", branded=False)

    result = verify_markdown_import(document, manifest)

    assert not result.content_preserved


def test_authored_vertical_tab_is_not_a_supported_markdown_docx_literal():
    with pytest.raises(UnsupportedSourceError):
        markdown_import_manifest('```python\nprint("a\vb")\n```', title="Code", branded=False)


_HARD_BREAK_SOURCE = "# Weekend notes\n\nWhen: Friday  \nWhere: [Park](https://example.com/park)\n"


def _hard_break_document(separator: str) -> dict[str, JsonValue]:
    lines = ("When: Friday", "Where: ", "Park", "\n")
    contents = (lines[0] + separator + lines[1], lines[2], lines[3])
    styles: tuple[dict[str, JsonValue], ...] = (
        {},
        {"link": {"url": "https://example.com/park"}},
        {},
    )
    start = 16
    elements: list[JsonValue] = []
    for content, style in zip(contents, styles, strict=True):
        elements.append(
            {
                "startIndex": start,
                "endIndex": start + len(content),
                "textRun": {"content": content, "textStyle": style},
            }
        )
        start += len(content)
    return {
        "revisionId": "native-hard-break-fixture",
        "tabs": [
            {
                "tabProperties": {"tabId": "t.0"},
                "documentTab": {
                    "body": {
                        "content": [
                            {"endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
                            {
                                "startIndex": 1,
                                "endIndex": 16,
                                "paragraph": {
                                    "elements": [
                                        {
                                            "startIndex": 1,
                                            "endIndex": 16,
                                            "textRun": {
                                                "content": "Weekend notes\n",
                                                "textStyle": {},
                                            },
                                        }
                                    ],
                                    "paragraphStyle": {"namedStyleType": "HEADING_1"},
                                },
                            },
                            {
                                "startIndex": 16,
                                "endIndex": start,
                                "paragraph": {
                                    "elements": elements,
                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                                },
                            },
                        ]
                    }
                },
            }
        ],
    }


def test_markdown_hard_break_imported_as_native_line_break_verifies():
    manifest = markdown_import_manifest(_HARD_BREAK_SOURCE, title="Weekend notes", branded=False)

    result = verify_markdown_import(_hard_break_document("\v"), manifest)

    assert result.content_preserved
    assert result.structure_preserved


@pytest.mark.parametrize("separator", [" ", "", "\t"])
def test_markdown_hard_break_equivalence_cannot_hide_a_lost_break(separator: str):
    manifest = markdown_import_manifest(_HARD_BREAK_SOURCE, title="Weekend notes", branded=False)

    result = verify_markdown_import(_hard_break_document(separator), manifest)

    assert not result.content_preserved
