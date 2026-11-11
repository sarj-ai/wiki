from dataclasses import replace
from typing import override

from pydantic import JsonValue
import pytest

from app.document_transform.ir import (
    MAX_DOCUMENT_BLOCKS,
    DocumentIR,
    parse_google_document,
    parse_markdown,
    utf16_length,
)
from app.document_transform.models import PatchGenerator, RewriteReview
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.quotations import quoted_block_ids
from app.document_transform.readability_json import array, obj
from app.document_transform.readability_paragraphs import readability_document
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.document_transform.unslop import model_editable_blocks
from app.identifiers import FileId


_FIRST = "The release document was reviewed by the team."
_LAST = "The release document was approved by the team."
_OUTSIDE = "The release document was stored by the team."
_REWRITES = {
    _FIRST: "The team reviewed the release document.",
    _LAST: "The team approved the release document.",
    _OUTSIDE: "The team stored the release document.",
}
_DELIMITERS = [('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’")]


def _native_document(texts: tuple[str, ...]) -> dict[str, JsonValue]:
    paragraphs: list[JsonValue] = []
    cursor = 1
    for text in texts:
        end = cursor + utf16_length(text) + 1
        paragraphs.append(
            {
                "startIndex": cursor,
                "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT", "alignment": "JUSTIFIED"},
                    "elements": [
                        {
                            "startIndex": cursor,
                            "endIndex": end,
                            "textRun": {
                                "content": text + "\n",
                                "textStyle": {"weightedFontFamily": {"fontFamily": "Nunito"}},
                            },
                        }
                    ],
                },
            }
        )
        cursor = end
    return {
        "revisionId": "quotation-revision",
        "tabs": [
            {"tabProperties": {"tabId": "main"}, "documentTab": {"body": {"content": paragraphs}}}
        ],
    }


@pytest.mark.parametrize(("opening", "closing"), _DELIMITERS)
@pytest.mark.parametrize("separator", ["\n", "\r\n", "\u2028", "\u2029"])
def test_rewrite_constraints_preserve_a_complete_multiline_quotation(
    opening: str, closing: str, separator: str
) -> None:
    quotation = opening + _FIRST + separator + _LAST + closing
    source = "The reviewer retained " + quotation
    parsed = parse_markdown(_OUTSIDE, title="Quoted release")
    document = replace(parsed, blocks=(replace(parsed.blocks[0], text=source),))
    block = document.blocks[0]
    proposal = source.replace(_FIRST, _REWRITES[_FIRST]).replace(_LAST, _REWRITES[_LAST])
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=proposal)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert rewrite_constraints(source).quotations == (quotation,)
    assert not decision.accepted
    assert decision.findings[0].code == "quoted_content_changed"


@pytest.mark.parametrize(("opening", "closing"), _DELIMITERS)
def test_paragraph_quotation_protection_applies_to_native_docs_and_markdown(
    opening: str, closing: str
) -> None:
    texts = (
        opening + _FIRST,
        "The reviewer retains the team's measurements.",
        _LAST + closing,
        _OUTSIDE,
    )
    documents = (
        parse_markdown("\n".join(texts), title="Quoted release"),
        parse_google_document(_native_document(texts), title="Quoted release"),
        readability_document(_native_document(texts), title="Quoted release"),
    )
    for document in documents:
        assert tuple(block.text for block in model_editable_blocks(document)) == (_OUTSIDE,)
        decision = validate_patches(
            document,
            [
                TextPatch(
                    block_id=block.id,
                    expected_sha256=block.sha256,
                    replacement=block.text.replace(_FIRST, _REWRITES[_FIRST]).replace(
                        _LAST, _REWRITES[_LAST]
                    ),
                )
                for block in document.blocks
            ],
            phase=TransformPhase.UNSLOP,
            aggressive_unslop=True,
        )
        assert not decision.accepted
    assert [block.editable for block in documents[-1].blocks] == [False, False, False, True]


def test_quotation_scopes_do_not_join_unrelated_document_tabs() -> None:
    document = _native_document(("“" + _FIRST,))
    other = obj(array(_native_document((_LAST + "”",))["tabs"])[0])
    obj(other["tabProperties"])["tabId"] = "other"
    array(document["tabs"]).append(other)
    assert not quoted_block_ids(parse_google_document(document, title="Tabs"))


@pytest.mark.parametrize("unmatched", ['"', "'", "“", "‘"])
def test_unmatched_other_quote_styles_cannot_hide_a_cross_paragraph_quotation(
    unmatched: str,
) -> None:
    opening, closing = ("‘", "’") if unmatched in {'"', "“"} else ("“", "”")
    document = parse_markdown(
        "An unmatched mark follows: "
        + unmatched
        + "\n"
        + opening
        + _FIRST
        + "\n"
        + _LAST
        + closing
        + "\n"
        + _OUTSIDE,
        title="Mixed quotation styles",
    )
    assert quoted_block_ids(document) == frozenset(block.id for block in document.blocks[1:3])


def test_nested_quote_styles_preserve_the_outer_quotation() -> None:
    document = parse_markdown(
        '“The reviewer said "release document".\n' + _LAST + "”\n" + _OUTSIDE,
        title="Nested quotations",
    )
    assert quoted_block_ids(document) == frozenset(block.id for block in document.blocks[:2])


@pytest.mark.parametrize(("opening", "closing"), [("'", "'"), ("‘", "’")])
@pytest.mark.parametrize("label", ["notes", "`notes`"])
def test_plural_possessives_inside_a_quotation_do_not_end_its_protection(
    opening: str, closing: str, label: str
) -> None:
    texts = (
        opening + "The reviewers" + closing + " " + label + " retain 1.2300 ms.",
        _LAST + closing,
        _OUTSIDE,
    )
    source = "\n".join(texts)
    assert rewrite_constraints(source).quotations == ("\n".join(texts[:2]),)
    for document in (
        parse_markdown(source, title="Possessive quotation"),
        parse_google_document(_native_document(texts), title="Possessive quotation"),
    ):
        assert quoted_block_ids(document) == frozenset(block.id for block in document.blocks[:2])
        assert tuple(block.text for block in model_editable_blocks(document)) == (_OUTSIDE,)


def test_repeated_plural_possessives_protect_the_whole_quotation() -> None:
    texts = ["‘The reviewers’ notes retain 1.2300 ms."]
    texts.extend(["The reviewers’ notes retain 1.2300 ms."] * (MAX_DOCUMENT_BLOCKS - 1))
    texts[-1] += "’"
    document = parse_markdown("\n".join(texts), title="Long quotation")
    assert quoted_block_ids(document) == frozenset(block.id for block in document.blocks)


@pytest.mark.parametrize(("opening", "closing"), [("'", "'"), ("‘", "’")])
def test_distinct_quoted_labels_do_not_freeze_intervening_paragraphs(
    opening: str, closing: str
) -> None:
    source = opening + "status" + closing + "\n" + _OUTSIDE + "\n" + opening + "cost" + closing
    document = parse_markdown(source, title="Quoted labels")
    assert not quoted_block_ids(document)
    assert rewrite_constraints(source).quotations == (
        f"{opening}status{closing}",
        f"{opening}cost{closing}",
    )


def test_a_later_curly_opener_scopes_an_otherwise_unmatched_mark() -> None:
    document = parse_markdown(
        "An unmatched mark follows: “\n“" + _FIRST + "\n" + _LAST + "”\n" + _OUTSIDE,
        title="Repeated opener",
    )
    assert quoted_block_ids(document) == frozenset(block.id for block in document.blocks[1:3])


@pytest.mark.parametrize("literal", ['`print("hello")`', "`value='team'`", '``print("hello")``'])
def test_code_quotes_do_not_hide_unquoted_prose(literal: str) -> None:
    document = parse_markdown(literal + "\n" + _OUTSIDE, title="Code")
    assert not quoted_block_ids(document)


def test_a_quote_in_code_cannot_close_a_prose_quotation() -> None:
    document = parse_markdown(
        "“" + _FIRST + '\n`print("”")`\n' + _LAST + "”\n" + _OUTSIDE, title="Quoted code"
    )
    assert quoted_block_ids(document) == frozenset(block.id for block in document.blocks[:3])


def test_contractions_and_possessives_do_not_open_cross_paragraph_quotes() -> None:
    document = parse_markdown(
        "The team's reviewer doesn't approve this release.\nThe reviewers' notes contain enough data.\n"
        + _OUTSIDE,
        title="Apostrophes",
    )
    assert not quoted_block_ids(document)


def test_unmatched_opening_marks_in_a_maximum_size_document_do_not_rescan_suffixes() -> None:
    source = "\n".join(["“" * 200] * 750)
    assert not quoted_block_ids(parse_markdown(source, title="Unmatched quotation marks"))


class _Editor(PatchGenerator):
    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ("controlled-quotation-editor",)

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=_REWRITES.get(block.text, block.text),
            )
            for block in model_editable_blocks(document)
        ]

    @override
    async def review(self, document: DocumentIR, patches: list[TextPatch]) -> RewriteReview:
        return RewriteReview(frozenset(patch.block_id for patch in patches), {})


async def test_editorial_pipeline_preserves_paragraph_quotes_and_still_edits_unquoted_text() -> (
    None
):
    source = "“It should be noted that " + _FIRST + "\n" + _LAST + "”\n" + _OUTSIDE
    original = parse_markdown(source, title="Quoted release")
    outcome = await TextTransformPlanner(generator=_Editor(), allow_unslop_model=True).transform(
        original,
        DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown",
                name="Quoted release",
                content=source,
                destination_folder_id=FileId("disposable"),
            ),
            mode=TransformMode.UNSLOP,
        ),
    )
    assert [block.text for block in outcome.document.blocks] == [
        block.text for block in original.blocks[:2]
    ] + [_REWRITES[_OUTSIDE]]
    assert outcome.deterministic_count == 0
    assert outcome.model_count == 1
