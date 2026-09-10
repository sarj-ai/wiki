from copy import deepcopy
from io import BytesIO
from typing import Any
import uuid
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring
from docx import Document as WordDocument
import pytest

from app.document_transform.brand_manifest import HEADER_TITLE_SEPARATOR
from app.document_transform.docx_renderer import markdown_import_manifest, render_markdown_docx
from app.document_transform.executor import DocumentTransformExecutor
from app.document_transform.gateway import OutputDraft
from app.document_transform.ir import utf16_length
from app.document_transform.markdown_import import (
    markdown_import_brand_diagnostics,
    verify_markdown_import,
)
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
from app.identifiers import FileId, TaskId
from tests.test_document_transform import _CopyCheckpointStore, _Gateway, _Generator
from tests.test_document_transform_docx import LOGO_BYTES, WP, W
from tests.test_markdown_import import _branded_document


def _titled_document(title: str, *, newline: bool) -> dict[str, Any]:
    document = _branded_document()
    paragraph = document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"][0]["paragraph"]
    width = utf16_length(title + HEADER_TITLE_SEPARATOR)
    elements = [
        {
            "startIndex": 0,
            "endIndex": width,
            "textRun": {
                "content": title + HEADER_TITLE_SEPARATOR,
                "textStyle": deepcopy(paragraph["elements"][0]["textRun"]["textStyle"]),
            },
        },
        {
            "startIndex": width,
            "endIndex": width + 1,
            "inlineObjectElement": {"inlineObjectId": "logo-1"},
        },
    ]
    if newline:
        elements.append(
            {"startIndex": width + 1, "endIndex": width + 2, "textRun": {"content": "\n"}}
        )
    document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"] = [
        {
            "startIndex": 0,
            "endIndex": width + 1 + int(newline),
            "paragraph": {
                "elements": elements,
                "paragraphStyle": deepcopy(paragraph["paragraphStyle"]),
            },
        }
    ]
    return document


@pytest.mark.parametrize("newline", [False, True])
@pytest.mark.parametrize("title", ["Chosen Title — Sarj", "Safety 🚀 Review", "مراجعة"])
def test_native_header_exact_title_preserves_body(*, title: str, newline: bool) -> None:
    document = _titled_document(title, newline=newline)
    before = deepcopy(document)
    manifest = markdown_import_manifest("Body", title="Import", branded=True, header_title=title)
    result = verify_markdown_import(document, manifest)
    assert result.brand_applied
    assert result.content_preserved
    assert result.structure_preserved
    assert document == before


@pytest.mark.parametrize(
    "change",
    [
        "missing_title",
        "wrong_title",
        "wrong_order",
        "extra_text",
        "link",
        "suggestion",
        "gap",
        "wrong_width",
        "outer_end",
        "negative",
        "boolean",
        "extra_logo",
    ],
)
def test_native_header_rejects_changed_or_unproven_title(change: str) -> None:
    title = "Safety 🚀 Review"
    document = _titled_document(title, newline=True)
    outer = document["tabs"][0]["documentTab"]["headers"]["header-1"]["content"][0]
    runs = outer["paragraph"]["elements"]
    match change:
        case "missing_title":
            runs.pop(0)
        case "wrong_title":
            runs[0]["textRun"]["content"] = "Other\t"
        case "wrong_order":
            runs.reverse()
        case "extra_text":
            runs[-1]["textRun"]["content"] = "EXTRA\n"
        case "link":
            runs[0]["textRun"]["textStyle"] = {"link": {"url": "https://example.com/private"}}
        case "suggestion":
            runs[0]["suggestedInsertionIds"] = ["private"]
        case "gap":
            for run in runs[1:]:
                run["startIndex"] += 1
                run["endIndex"] += 1
            outer["endIndex"] += 1
        case "wrong_width":
            runs[0]["endIndex"] -= 1
        case "outer_end":
            outer["endIndex"] += 1
        case "negative":
            runs[0]["startIndex"] = -1
        case "boolean":
            runs[0]["startIndex"] = False
        case _:
            runs.insert(1, deepcopy(runs[1]))
    manifest = markdown_import_manifest("Body", title="Import", branded=True, header_title=title)
    result = verify_markdown_import(document, manifest)
    assert not result.brand_applied
    assert result.content_preserved
    assert result.structure_preserved is (change != "extra_logo")


def test_writer_native_header_uses_chosen_title_without_rewriting_authored_h1() -> None:
    data = render_markdown_docx(
        "# authored source heading\n\nBody",
        title="Original Name",
        header_title="Chosen Title — Sarj",
        logo_bytes=LOGO_BYTES,
    )
    with ZipFile(BytesIO(data)) as archive:
        header = fromstring(archive.read("word/header1.xml"))
        body = fromstring(archive.read("word/document.xml"))
    assert "".join(node.text or "" for node in header.findall(f".//{W}t")) == (
        "Chosen Title — Sarj" + HEADER_TITLE_SEPARATOR.removesuffix("\t")
    )
    assert header.find(f".//{W}tab") is not None
    assert header.find(f".//{WP}docPr") is not None
    assert "Chosen Title" not in "".join(node.text or "" for node in body.findall(f".//{W}t"))
    assert "authored source heading" in "".join(
        node.text or "" for node in body.findall(f".//{W}t")
    )
    assert data == render_markdown_docx(
        "# authored source heading\n\nBody",
        title="Original Name",
        header_title="Chosen Title — Sarj",
        logo_bytes=LOGO_BYTES,
    )


async def test_checkpoint_header_title_ignores_provider_collision_name_and_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _Gateway()
    store = _CopyCheckpointStore()
    task_id = TaskId(uuid.uuid7())
    original_create = gateway._create

    def create(source_sha256: str, output_name: str) -> OutputDraft:
        return original_create(source_sha256, f"{output_name} (2)")

    monkeypatch.setattr(gateway, "_create", create)
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# authored heading\n\nBody",
            name="Source",
            destination_folder_id=FileId("folder"),
        ),
        mode=TransformMode.FORMAT,
        output_name="chosen title",
    )
    executor = DocumentTransformExecutor(
        gateway=gateway,
        generator=_Generator(),
        checkpoint_store=store,
        task_id=task_id,
        lease_token=uuid.uuid7(),
        logo_bytes=LOGO_BYTES,
    )
    result = await executor.execute(operation_id=task_id, request=request)
    assert result.output.name == "Chosen Title (2)"
    assert store.checkpoints[-1].output_name == "Chosen Title"
    assert set(gateway.verified_header_titles) == {"Chosen Title"}
    assert gateway.imported_docx is not None
    assert (
        WordDocument(BytesIO(gateway.imported_docx)).sections[0].header.paragraphs[0].text
        == "Chosen Title" + HEADER_TITLE_SEPARATOR
    )
    assert (
        await DocumentTransformExecutor(
            gateway=gateway,
            generator=_Generator(),
            checkpoint_store=store,
            task_id=task_id,
            lease_token=uuid.uuid7(),
            logo_bytes=LOGO_BYTES,
            checkpoint=store.checkpoints[-1],
            checkpoint_generation=store.generation,
        ).execute(operation_id=task_id, request=request)
        == result
    )
    assert gateway.applies == 1
    assert store.resources == [result.output.file_id]
    assert set(gateway.verified_header_titles) == {"Chosen Title"}


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "font",
        "size",
        "color",
        "bold",
        "alignment",
        "missing",
        "huge",
        "null",
        "inherited",
        "duplicate_parent",
        "wrong_parent",
    ],
)
def test_header_title_typography_requires_explicit_or_unique_parent_proof(change: str) -> None:
    document = _titled_document("Chosen Title", newline=True)
    tab = document["tabs"][0]["documentTab"]
    paragraph = tab["headers"]["header-1"]["content"][0]["paragraph"]
    style = paragraph["elements"][0]["textRun"]["textStyle"]
    if change == "font":
        style["weightedFontFamily"]["fontFamily"] = "Comic Sans MS"
    elif change == "size":
        style["fontSize"]["magnitude"] = 48
    elif change == "color":
        style["foregroundColor"]["color"]["rgbColor"]["red"] = 1
    elif change == "bold":
        style["bold"] = False
    elif change == "alignment":
        paragraph["paragraphStyle"]["alignment"] = "START"
    elif change == "missing":
        style.clear()
    elif change == "huge":
        style["foregroundColor"]["color"]["rgbColor"]["red"] = 10**400
    elif change == "null":
        style["fontSize"] = None
    elif change in {"inherited", "duplicate_parent", "wrong_parent"}:
        parent: dict[str, Any] = {
            "namedStyleType": "NORMAL_TEXT",
            "textStyle": deepcopy(style),
            "paragraphStyle": deepcopy(paragraph["paragraphStyle"]),
        }
        tab["namedStyles"] = {"styles": [parent]}
        style.clear()
        paragraph["paragraphStyle"] = {"namedStyleType": "NORMAL_TEXT"}
        if change == "duplicate_parent":
            tab["namedStyles"]["styles"].append(deepcopy(parent))
        elif change == "wrong_parent":
            parent["textStyle"]["weightedFontFamily"]["fontFamily"] = "Arial"
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    result = verify_markdown_import(document, manifest)
    assert result.brand_applied is (change in {"none", "inherited"})
    assert result.content_preserved
    assert result.structure_preserved
    checks = markdown_import_brand_diagnostics(document, manifest)
    assert checks["import_header_title_style"] is (change in {"none", "inherited"})
    assert all(type(value) is bool for value in checks.values())


@pytest.mark.parametrize("inherited", [False, True])
@pytest.mark.parametrize(
    ("patch", "valid"),
    [
        pytest.param({"italic": "SECRET"}, False, id="malformed-italic"),
        pytest.param({"underline": 1}, False, id="malformed-underline"),
        pytest.param({"strikethrough": None}, False, id="malformed-strike"),
        pytest.param({"smallCaps": True}, False, id="small-caps"),
        pytest.param({"baselineOffset": "SUPERSCRIPT"}, False, id="superscript"),
        pytest.param({"baselineOffset": "NORMAL"}, False, id="invalid-baseline-enum"),
        pytest.param(
            {"weightedFontFamily": {"fontFamily": "Nunito", "weight": 900}},
            False,
            id="heavy-weight",
        ),
        pytest.param(
            {"weightedFontFamily": {"fontFamily": "Nunito", "weight": True}},
            False,
            id="bool-weight",
        ),
        pytest.param(
            {"weightedFontFamily": {"fontFamily": "Nunito", "weight": 700}}, True, id="bold-weight"
        ),
        pytest.param(
            {"weightedFontFamily": {"fontFamily": "Nunito", "weight": 400}},
            True,
            id="normal-weight-bold",
        ),
        pytest.param(
            {"backgroundColor": {"color": {"rgbColor": {"red": 1}}}}, False, id="highlight"
        ),
        pytest.param({"backgroundColor": {}}, True, id="transparent-background"),
        pytest.param(
            {
                "italic": False,
                "underline": False,
                "strikethrough": False,
                "smallCaps": False,
                "baselineOffset": "NONE",
            },
            True,
            id="explicit-defaults",
        ),
        pytest.param(
            {
                "foregroundColor": {
                    "color": {
                        "rgbColor": {
                            "red": 115 / 255,
                            "green": 115 / 255,
                            "blue": 115 / 255,
                            "SECRET": 0,
                        }
                    }
                }
            },
            False,
            id="unknown-rgb-key",
        ),
        pytest.param(
            {"fontSize": {"unit": "PT", "magnitude": 9, "SECRET": 0}},
            False,
            id="unknown-dimension-key",
        ),
    ],
)
def test_header_title_rejects_noncanonical_effects_and_malformed_values(
    patch: dict[str, Any], *, valid: bool, inherited: bool
) -> None:
    document = _titled_document("Chosen Title", newline=True)
    tab = document["tabs"][0]["documentTab"]
    run = tab["headers"]["header-1"]["content"][0]["paragraph"]["elements"][0]["textRun"]
    run["textStyle"].update(patch)
    if inherited:
        tab["namedStyles"] = {
            "styles": [{"namedStyleType": "NORMAL_TEXT", "textStyle": deepcopy(run["textStyle"])}]
        }
        run["textStyle"] = {}
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    assert verify_markdown_import(document, manifest).brand_applied is valid
    assert (
        markdown_import_brand_diagnostics(document, manifest)["import_header_title_style"] is valid
    )


@pytest.mark.parametrize("provided", ["duplicate", "null", "malformed", "missing_normal"])
def test_ambiguous_parent_never_erases_inherited_effects(provided: str) -> None:
    document = _titled_document("Chosen Title", newline=True)
    tab = document["tabs"][0]["documentTab"]
    parent = {"namedStyleType": "NORMAL_TEXT", "textStyle": {"italic": True}}
    match provided:
        case "duplicate":
            tab["namedStyles"] = {"styles": [parent, deepcopy(parent)]}
        case "null":
            tab["namedStyles"] = None
        case "malformed":
            tab["namedStyles"] = {"styles": "SECRET"}
        case _:
            tab["namedStyles"] = {"styles": [{"namedStyleType": "HEADING_1"}]}
    manifest = markdown_import_manifest(
        "Body", title="Import", branded=True, header_title="Chosen Title"
    )
    assert not verify_markdown_import(document, manifest).brand_applied
    assert (
        markdown_import_brand_diagnostics(document, manifest)["import_header_title_style"] is False
    )
