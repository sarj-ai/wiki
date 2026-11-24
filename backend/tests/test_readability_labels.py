from copy import deepcopy
from itertools import product
from typing import Literal

from pydantic import JsonValue
import pytest

from app.document_transform.readability_json import array, bodies, content, obj
from app.document_transform.readability_paragraphs import (
    readability_document,
    validated_readability_paragraphs,
)
from app.document_transform.readability_types import ReadabilityPart, ReadabilityProposal
from tests.test_readability_lists import _prose_document


def _evidence_document(label: str) -> dict[str, JsonValue]:
    document = _prose_document(
        tuple(
            text
            for section in range(7)
            for text in (
                f"Section {section}",
                f"Record TEST-{section} measured 12.50 ms across 24 samples.",
                label,
                f"First record TEST-{section}-A",
                f"Second record TEST-{section}-B",
                label,
                f"First record TEST-{section}-C",
                f"Second record TEST-{section}-D",
            )
        )
    )
    for _, tab in bodies(document):
        tab["lists"] = {"evidence": {"listProperties": {"nestingLevels": [{"glyphSymbol": "●"}]}}}
        for index, value in enumerate(content(tab)):
            paragraph = obj(obj(value)["paragraph"])
            if index % 8 == 0:
                obj(paragraph["paragraphStyle"])["namedStyleType"] = "HEADING_3"
            elif index % 8 in {3, 4, 6, 7}:
                paragraph["bullet"] = {"listId": "evidence", "nestingLevel": 0}
                run = obj(obj(array(paragraph["elements"])[0])["textRun"])
                obj(run["textStyle"])["link"] = {
                    "url": f"https://example.com/records/TEST-{index}?samples=24"
                }
    return document


@pytest.mark.parametrize(
    ("role", "label"),
    list(product(("heading2", "heading3"), ("Evidence:", "Supporting records"))),
)
def test_repeated_labels_before_link_lists_stay_body_text(
    label: str, role: Literal["heading2", "heading3"]
) -> None:
    document = _evidence_document(label)
    original = deepcopy(document)
    ir = readability_document(document, title="Evidence report")
    proposals = [
        ReadabilityProposal(
            block_id=block.id,
            source_sha256=block.sha256,
            parts=(ReadabilityPart(text=block.text, role=role),),
        )
        for block in ir.blocks
        if block.text == label
    ]
    assert len(proposals) == 14

    with pytest.raises(ValueError, match=r"label|lead-in"):
        validated_readability_paragraphs(document, ir, proposals)

    assert document == original


@pytest.mark.parametrize("label", ["Evidence:", "Supporting records:", "Notes:"])
def test_short_colon_label_stays_body_text_without_a_list(label: str) -> None:
    document = _prose_document((label, "Record TEST-01 measured 12.50 ms."))
    ir = readability_document(document, title="Labeled prose")
    block = ir.blocks[0]
    proposal = ReadabilityProposal(
        block_id=block.id,
        source_sha256=block.sha256,
        parts=(ReadabilityPart(text=label, role="heading3"),),
    )

    with pytest.raises(ValueError, match=r"label|lead-in"):
        validated_readability_paragraphs(document, ir, [proposal])


def test_split_list_cannot_promote_its_lead_in() -> None:
    parts = (
        ReadabilityPart(text="Supporting records", role="heading3"),
        ReadabilityPart(text="Record TEST-01 measured 12.50 ms.", role="bullet"),
        ReadabilityPart(text="Record TEST-02 measured 24.00 ms.", role="bullet"),
    )
    document = _prose_document((" ".join(part.text for part in parts),))
    ir = readability_document(document, title="List with a lead-in")
    block = ir.blocks[0]
    proposal = ReadabilityProposal(block_id=block.id, source_sha256=block.sha256, parts=parts)

    with pytest.raises(ValueError, match=r"label|lead-in"):
        validated_readability_paragraphs(document, ir, [proposal])


def test_bold_labels_and_their_link_lists_keep_their_authored_styles() -> None:
    document = _evidence_document("Evidence:")
    for _, tab in bodies(document):
        for index, value in enumerate(content(tab)):
            if index % 8 in {2, 5}:
                run = obj(obj(array(obj(obj(value)["paragraph"])["elements"])[0])["textRun"])
                obj(run["textStyle"])["bold"] = True
    original = deepcopy(document)

    ir = readability_document(document, title="Bold evidence labels")

    assert all(not block.editable for block in ir.blocks if block.text == "Evidence:")
    assert not validated_readability_paragraphs(document, ir, [])
    assert document == original
