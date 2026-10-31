from copy import deepcopy

import pytest

from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_visual_authority import visual_authority_block_ids
from tests.test_native_visual_authority import _document, _paragraph


@pytest.mark.parametrize("body_size", [12, 24])
@pytest.mark.parametrize("explicit_cleared_link", [False, True])
def test_protected_citation_link_color_does_not_turn_body_prose_into_a_manual_title(
    body_size: int, *, explicit_cleared_link: bool
):
    document = _document(body_size)
    paragraph = _paragraph(document)
    paragraph["paragraphStyle"]["alignment"] = "START"
    prose = (
        "Researchers report a cautious finding from the observed sample and retain its "
        "limits in the accompanying source. "
    )
    citation = "Reference"
    style = deepcopy(paragraph["elements"][0]["textRun"]["textStyle"])
    style["foregroundColor"] = {"color": {"rgbColor": {}}}
    if explicit_cleared_link:
        style["link"] = None
    prose_end = 1 + utf16_length(prose)
    citation_end = prose_end + utf16_length(citation)
    paragraph["elements"] = [
        {
            "startIndex": 1,
            "endIndex": prose_end,
            "textRun": {"content": prose, "textStyle": style},
        },
        {
            "startIndex": prose_end,
            "endIndex": citation_end,
            "textRun": {
                "content": citation,
                "textStyle": {
                    **style,
                    "foregroundColor": {"color": {"rgbColor": {"blue": 1}}},
                    "link": {"url": "https://example.test/study"},
                },
            },
        },
        {
            "startIndex": citation_end,
            "endIndex": citation_end + 1,
            "textRun": {"content": "\n", "textStyle": style},
        },
    ]
    document["tabs"][0]["documentTab"]["body"]["content"][0]["endIndex"] = citation_end + 1
    original = parse_google_document(document, title="Source")
    assert original.blocks[0].editable
    assert original.blocks[0].protected_suffix == citation
    assert visual_authority_block_ids(document, original) == (("b0",) if body_size == 24 else ())
