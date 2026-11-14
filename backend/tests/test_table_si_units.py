from io import BytesIO
import subprocess
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.ir import parse_markdown
from app.document_transform.readability_json import obj
from app.document_transform.readability_tables import plan_readable_tables, readable_table_requests
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.table_layout import numeric_table_value
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    "quantity",
    [
        "5 μg",
        "5 µg",
        "2 nm",
        "2.00 pF",
        "0.125 ± 0.004 μg",
        "1.20e-6 nF",
        "1/2 μL",
        "3 pm",
        "2.00 GΩ",
        "10 daPa",
        "3 mSv",
        "2 kat",
        "5 kJ/(kg K)",
        "12.3 μmol m⁻² s⁻¹",
        "1 nm–2 nm",
        "QAR 1.2300",
        "2 min",
        "5 mL",
        "3 N µm",
    ],
)
def test_prefixed_si_quantities_align_in_native_and_imported_tables(quantity: str) -> None:
    rows = (("Mass", quantity), ("Missing", "—"))
    target = plan_readable_tables(_native_table(rows))[0]
    assert target.numeric == {1}
    alignments = [
        obj(obj(request["updateParagraphStyle"])["paragraphStyle"])["alignment"]
        for request in readable_table_requests(_native_table(rows), (target,))
        if "updateParagraphStyle" in request
    ]
    assert alignments == ["START", "END"] * len(rows)
    source = f"| Property | Reading |\n| --- | --- |\n| Mass | {quantity} |\n| Missing | — |"
    document = Document(BytesIO(render_markdown_docx(source, title="SI measurements")))
    cell = document.tables[0].cell(1, 1)
    assert cell.text == quantity
    assert cell.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT


@pytest.mark.parametrize("quantity", ["2 nf", "5 UG", "3 gω", "5 μg observed", "12 apples"])
def test_new_unit_symbols_remain_case_sensitive_and_do_not_consume_prose(quantity: str) -> None:
    assert not numeric_table_value(quantity)


def test_prefixed_si_symbols_do_not_absorb_a_following_prose_clause() -> None:
    assert rewrite_constraints("The team measured 3 m as recorded.").protected_tokens == ("3 m",)
    assert rewrite_constraints("The team measured 5 kJ/(kg K).").protected_tokens == (
        "5 kJ/(kg K)",
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("3 N µm", "3 N"),
        ("2 V nF", "2 V pF"),
        ("2 V nF and 3 V pF", "2 V pF and 3 V nF"),
        ("3 m as^-2", "3 m"),
        ("3 m as²", "3 m"),
    ],
)
def test_compound_si_units_survive_editorial_reordering(before: str, after: str) -> None:
    document = parse_markdown(
        f"The reading was measured by the team at {before}.", title="Compound units"
    )
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement=f"The team measured the reading at {after}.",
    )
    rejected = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert not rejected.accepted
    assert [finding.code for finding in rejected.findings] == ["protected_content_changed"]
    faithful = patch.model_copy(
        update={"replacement": f"The team measured the reading at {before}."}
    )
    assert validate_patches(
        document, [faithful], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    ).accepted == {block.id: faithful.replacement}


def test_failed_repeated_unit_chains_finish_within_a_bounded_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.document_transform.table_layout import numeric_table_value; "
                "print([numeric_table_value('1 ' + unit * 64 + 'notes') "
                "for unit in ('m ', 'nm ', 'min ')])"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.stdout.strip() == "[False, False, False]"
