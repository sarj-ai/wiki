from copy import deepcopy
from typing import override

from pydantic import JsonValue
import pytest

from app.document_transform.ir import DocumentIR, utf16_length
from app.document_transform.readability import plan_readability
from app.document_transform.readability_json import array, bodies, content, integer, obj
from app.document_transform.readability_paragraphs import (
    readability_document,
    validated_readability_paragraphs,
)
from app.document_transform.readability_types import ReadabilityPart, ReadabilityProposal
from tests.test_document_readability import _fixture, _LayoutEditor, _paragraph


def _document(texts: tuple[str, ...]) -> dict[str, JsonValue]:
    document = _fixture("before")
    template = _paragraph(document, 19)
    elements: list[JsonValue] = []
    start = 1
    for text in texts:
        element = deepcopy(template)
        end = start + utf16_length(text + "\n")
        element.update({"startIndex": start, "endIndex": end})
        run = obj(array(obj(element["paragraph"])["elements"])[0])
        run.update({"startIndex": start, "endIndex": end})
        obj(run["textRun"])["content"] = text + "\n"
        elements.append(element)
        start = end
    obj(next(bodies(document))[1]["body"])["content"] = elements
    return document


def _headings(ir: DocumentIR, roles: tuple[str, ...]) -> list[ReadabilityProposal]:
    return [
        ReadabilityProposal(
            block_id=block.id,
            source_sha256=block.sha256,
            parts=(ReadabilityPart.model_validate({"text": block.text, "role": role}),),
        )
        for block, role in zip(ir.blocks, roles, strict=False)
    ]


@pytest.mark.parametrize("texts", [("Replay requires a valid lease",), ("Overview", "Recovery")])
def test_new_headings_require_following_content(texts: tuple[str, ...]) -> None:
    document = _document(texts)
    ir = readability_document(document, title="QA reference")
    proposals = _headings(ir, ("heading2",) * len(texts))

    with pytest.raises(ValueError, match="must introduce content"):
        validated_readability_paragraphs(document, ir, proposals)


@pytest.mark.parametrize("nested", [False, True])
def test_new_labels_can_introduce_body_content(nested: bool) -> None:
    texts: tuple[str, ...] = ("Recovery", "The worker saves the checkpoint.")
    if nested:
        texts = ("Operations", *texts)
    document = _document(texts)
    ir = readability_document(document, title="QA reference")
    proposals = _headings(ir, ("heading2", "heading3") if nested else ("heading2",))

    assert len(validated_readability_paragraphs(document, ir, proposals)) == len(proposals)


def test_blank_paragraph_does_not_supply_heading_content() -> None:
    document = _document(("Recovery", ""))
    ir = readability_document(document, title="QA reference")

    with pytest.raises(ValueError, match="must introduce content"):
        validated_readability_paragraphs(document, ir, _headings(ir, ("heading2",)))


def test_body_in_another_tab_cannot_supply_heading_content() -> None:
    document = _document(("Recovery",))
    other = deepcopy(obj(array(_document(("The worker saves the checkpoint.",))["tabs"])[0]))
    obj(other["tabProperties"])["tabId"] = "second-tab"
    array(document["tabs"]).append(other)
    ir = readability_document(document, title="QA reference")

    with pytest.raises(ValueError, match="must introduce content"):
        validated_readability_paragraphs(document, ir, _headings(ir, ("heading2",)))


def test_existing_authored_orphan_heading_is_preserved() -> None:
    document = _document(("Authored heading",))
    obj(obj(_paragraph(document, 1)["paragraph"])["paragraphStyle"])["namedStyleType"] = "HEADING_2"
    ir = readability_document(document, title="QA reference")

    assert not validated_readability_paragraphs(document, ir, [])


@pytest.mark.parametrize("next_heading", [False, True])
def test_native_table_supplies_heading_content(next_heading: bool) -> None:
    texts = ("Measurements", "Recovery", "The worker saves the checkpoint.")
    document = _document(texts if next_heading else texts[:1])
    table = deepcopy(_paragraph(_fixture("before"), 247))
    content(next(bodies(document))[1]).insert(1, table)
    ir = readability_document(document, title="QA reference")
    proposals = _headings(ir, ("heading2", "heading2") if next_heading else ("heading2",))

    assert len(validated_readability_paragraphs(document, ir, proposals)) == len(proposals)


@pytest.mark.parametrize("object_key", ["inlineObjectElement", "pageBreak", "columnBreak"])
@pytest.mark.parametrize("whitespace", ["", "   "])
def test_inline_object_with_whitespace_supplies_content_but_breaks_do_not(
    object_key: str, whitespace: str
) -> None:
    document = _document(("Measurements", whitespace))
    element = obj(content(next(bodies(document))[1])[1])
    paragraph = obj(element["paragraph"])
    run = obj(array(paragraph["elements"])[0])
    start = integer(run["startIndex"])
    run.update({"startIndex": start + 1, "endIndex": integer(run["endIndex"]) + 1})
    element["endIndex"] = integer(element["endIndex"]) + 1
    payload: dict[str, JsonValue] = (
        {"inlineObjectId": "qa-image"} if object_key == "inlineObjectElement" else {}
    )
    paragraph["elements"] = [
        {"startIndex": start, "endIndex": start + 1, object_key: payload},
        run,
    ]
    ir = readability_document(document, title="QA reference")
    proposals = _headings(ir, ("heading2",))

    if object_key == "inlineObjectElement":
        assert len(validated_readability_paragraphs(document, ir, proposals)) == 1
    else:
        with pytest.raises(ValueError, match="must introduce content"):
            validated_readability_paragraphs(document, ir, proposals)


class _AllHeadingEditor(_LayoutEditor):
    @override
    async def plan_readability(
        self, document: DocumentIR, *, previous: list[ReadabilityProposal], feedback: str = ""
    ) -> list[ReadabilityProposal]:
        self.calls += 1
        return _headings(document, ("heading2",) * len(document.blocks))


async def test_refinement_rejects_a_document_with_only_new_headings() -> None:
    document = _document(("Replay requires a valid lease", "Rollback requires a checkpoint"))
    editor = _AllHeadingEditor()

    assert await plan_readability(document, title="QA reference", generator=editor) is None
    assert editor.calls == 2
