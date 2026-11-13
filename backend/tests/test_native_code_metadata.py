import pytest

from app.document_transform.ir import BlockKind, parse_google_document
from app.document_transform.native_code import plan_native_code_styles
from app.document_transform.native_code_source import native_code_groups
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.unslop import model_editable_blocks
from tests.test_native_code import _document


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ('```python title="broken.py"', "```"),
        ("~~~{.python #qa}", "~~~"),
        ("```custom/dsl", "```"),
        ("~~~lang `literal`", "~~~"),
        ('   ````python title="broken.py"\t', "  `````\t"),
    ],
)
def test_native_fence_metadata_protects_code_without_freezing_following_prose(
    opening: str, closing: str
) -> None:
    source = _document(
        [
            ("Ordinary opening prose.\n", "Arial"),
            (opening + "\n", "Arial"),
            ("It is important to note that malformed code stays literal: if :\n", "Arial"),
            (closing + "\n", "Arial"),
            ("Ordinary closing prose.\n", "Arial"),
        ]
    )

    document = parse_google_document(source, title="Literal code")
    assert [block.kind for block in document.blocks] == [
        BlockKind.PARAGRAPH,
        BlockKind.CODE,
        BlockKind.CODE,
        BlockKind.CODE,
        BlockKind.PARAGRAPH,
    ]
    assert model_editable_blocks(document) == (document.blocks[0], document.blocks[4])
    assert [block.text for block in document.blocks] == [
        "Ordinary opening prose.",
        opening,
        "It is important to note that malformed code stays literal: if :",
        closing,
        "Ordinary closing prose.",
    ]
    code = document.blocks[2]
    decision = validate_patches(
        document,
        [
            TextPatch(
                block_id=code.id,
                expected_sha256=code.sha256,
                replacement="Malformed code stays literal: if :",
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["protected_block"]


def test_native_metadata_fence_requires_an_empty_matching_close() -> None:
    source = _document(
        [
            ('````python title="qa.py"\n', "Arial"),
            ("```\n", "Arial"),
            ("~~~~\n", "Arial"),
            ("````still-code\n", "Arial"),
            ("print('exact')\n", "Arial"),
            ("`````\t\n", "Arial"),
            ("Editable prose.\n", "Arial"),
        ]
    )

    document = parse_google_document(source, title="Closing fence")
    assert all(block.kind is BlockKind.CODE for block in document.blocks[:-1])
    assert model_editable_blocks(document) == (document.blocks[-1],)
    groups = native_code_groups(source)
    assert len(groups) == 1
    assert groups[0].complete
    assert len(groups[0].paragraphs) == 6


def test_unclosed_metadata_fence_is_protected_without_syntax_mutations() -> None:
    source = _document([('```python title="qa.py"\n', "Arial"), ("def broken(:\n", "Arial")])

    document = parse_google_document(source, title="Interrupted source")
    assert not model_editable_blocks(document)
    assert all(block.kind is BlockKind.CODE for block in document.blocks)
    assert not native_code_groups(source)[0].complete
    assert not plan_native_code_styles(source, document, document).requests


def test_backticks_in_backtick_info_remain_inline_prose() -> None:
    source = _document([("```python `literal` is inline prose.\n", "Arial")])

    document = parse_google_document(source, title="Inline marker")
    assert document.blocks[0].kind is BlockKind.PARAGRAPH
    assert not native_code_groups(source)


def test_supported_language_with_metadata_keeps_syntax_formatting_and_literals() -> None:
    source = _document(
        [
            ('```python title="qa.py"\n', "Arial"),
            ('print("µs 😀")\n', "Arial"),
            ("```\n", "Arial"),
            ("Editable prose.\n", "Arial"),
        ]
    )

    document = parse_google_document(source, title="Metadata syntax")
    plan = plan_native_code_styles(source, document, document)
    assert len(plan.paragraphs) == 3
    assert {paragraph.language for paragraph in plan.paragraphs} == {"python"}
    assert {paragraph.body_ordinal for paragraph in plan.paragraphs} == {0, 1, 2}
    assert document.blocks[-1].editable
