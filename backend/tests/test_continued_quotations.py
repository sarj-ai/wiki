import pytest

from app.document_transform.ir import parse_google_document, parse_markdown
from app.document_transform.quotations import quoted_block_ids
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.unslop import model_editable_blocks
from tests.test_multiblock_quotations import _native_document


@pytest.mark.parametrize(("opening", "closing"), [("“", "”"), ("‘", "’")])
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("prefix", ["", "The reviewer said, "])
@pytest.mark.parametrize("possessive", [False, True])
@pytest.mark.parametrize("blank_paragraphs", [False, True])
def test_repeated_paragraph_openers_preserve_the_complete_quotation(
    *,
    opening: str,
    closing: str,
    native: bool,
    prefix: str,
    possessive: bool,
    blank_paragraphs: bool,
) -> None:
    first_source, draft = (
        ("The reviewers’ notes retain 1.2300 ms.", "The reviewers’ notes keep 1.2300 ms.")
        if possessive
        else (
            "The release document was reviewed by the team.",
            "The team reviewed the release document.",
        )
    )
    quoted = (
        f"{prefix}{opening}{first_source}",
        f"{opening}The service remained paused during the review.",
        f"{opening}The team approved the unchanged release document.{closing}",
    )
    outside = "The fallback route is maintained by the team."
    texts = (
        (quoted[0], "", quoted[1], "", quoted[2], outside)
        if blank_paragraphs
        else (*quoted, outside)
    )
    document = (
        parse_google_document(_native_document(texts), title="Continued quotation")
        if native
        else parse_markdown("\n".join(texts), title="Continued quotation")
    )
    first = document.blocks[0]

    decision = validate_patches(
        document,
        [
            TextPatch(
                block_id=first.id,
                expected_sha256=first.sha256,
                replacement=f"{prefix}{opening}{draft}",
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert decision.findings[0].code == "quoted_content_changed"
    assert quoted_block_ids(document) == frozenset(block.id for block in document.blocks[:-1])
    assert tuple(block.text for block in model_editable_blocks(document)) == (outside,)
