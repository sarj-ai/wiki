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
