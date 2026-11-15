from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.ir import parse_markdown
from app.document_transform.readability_tables import plan_readable_tables
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.table_layout import numeric_table_value
from tests.test_table_layout import _native_table


@pytest.mark.parametrize(
    "quantity",
    [
        "1.20 eV",
        "1.20 keV",
        "1.20 MeV",
        "1.20 GeV",
        "(1.20 ± 0.04) eV",
        "(1.20 +/- 0.04) keV",
        "(1,20 +/− 0,04) MeV",
        "(1.20e-3 ± 0.04e-3) Da",
        "(1.20 ± 0.04) kDa",
        "(1.20 ± 0.04) u",
        "(1.20 ± 0.04) au",
        "(1.20 ± 0.04) ha",
        "(1.20 ± 0.04) t",
        "(1.20 ± 0.04) Mt",
        "(1.20 ± 0.04) °",
        "(1.20 ± 0.04) dB",
        "(1.20 ± 0.04) Np",
        "(1.20 ± 0.04) eV/nm",
        "(1.20 ± 0.04) kDa mol⁻¹",
        "(١٢٫٣ ± ٠٫٤) eV",
    ],
)
def test_non_si_quantities_keep_their_symbols_in_constraints_and_table_layout(
    quantity: str,
) -> None:
    source = f"The team measured {quantity}."
    assert rewrite_constraints(source).protected_tokens == (quantity,)
    assert numeric_table_value(quantity)
    native = _native_table((("Reading", quantity), ("Missing", "—")))
    assert plan_readable_tables(native)[0].numeric == {1}
    markdown = f"| Sample | Reading |\n| --- | --- |\n| A | {quantity} |\n| B | — |"
    document = Document(BytesIO(render_markdown_docx(markdown, title="Measurements")))
    cell = document.tables[0].cell(1, 1)
    assert cell.text == quantity
    assert cell.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("(1.20 ± 0.04) eV", "(1.20 ± 0.04)"),
        ("(1.20 ± 0.04) eV", "(1.20 ± 0.04) keV"),
        ("(1.20 ± 0.04) eV/nm", "(1.20 ± 0.04) eV"),
        ("(1.20 ± 0.04) eV", "(1.2 ± 0.04) eV"),
        ("(1.20 ± 0.04) eV and (2.30 ± 0.05) keV", "(1.20 ± 0.04) keV and (2.30 ± 0.05) eV"),
        ("(1.20 ± 0.04) u", "(1.20 ± 0.04)"),
        ("(1.20 ± 0.04) au", "(1.20 ± 0.04)"),
        ("(1.20 ± 0.04) ha", "(1.20 ± 0.04) t"),
        ("(1.20 ± 0.04) °", "(1.20 ± 0.04)"),
        ("(1.20 ± 0.04) dB", "(1.20 ± 0.04) B"),
    ],
)
def test_non_si_units_cannot_be_dropped_changed_or_reattached(before: str, after: str) -> None:
    document = parse_markdown(
        f"The reading was measured by the team at {before}.", title="Measurements"
    )
    block = document.blocks[0]
    patch = TextPatch(
        block_id=block.id,
        expected_sha256=block.sha256,
        replacement=f"The team measured the reading at {after}.",
    )
    decision = validate_patches(
        document, [patch], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    )
    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["protected_content_changed"]
    faithful = patch.model_copy(
        update={"replacement": f"The team measured the reading at {before}."}
    )
    assert validate_patches(
        document, [faithful], phase=TransformPhase.UNSLOP, aggressive_unslop=True
    ).accepted == {block.id: faithful.replacement}


@pytest.mark.parametrize("value", ["1 ev", "1 kev", "1 da", "1 AU", "1 HA", "1 np", "1 eV notes"])
def test_non_si_symbols_do_not_case_fold_or_consume_prose(value: str) -> None:
    assert not numeric_table_value(value)


def test_grouped_non_si_quantity_leaves_a_following_prose_clause_editable() -> None:
    assert rewrite_constraints(
        "The team measured (1.20 ± 0.04) eV as recorded."
    ).protected_tokens == ("(1.20 ± 0.04) eV",)


@pytest.mark.parametrize("prose", ["at night", "as recorded", "at the source"])
def test_non_si_units_do_not_absorb_following_prepositions(prose: str) -> None:
    assert rewrite_constraints(f"The team measured 15 ms {prose}.").protected_tokens == ("15 ms",)
    assert rewrite_constraints(f"The team measured (1.20 ± 0.04) eV {prose}.").protected_tokens == (
        "(1.20 ± 0.04) eV",
    )


def test_powered_attotonne_remains_a_unit_in_a_compound_quantity() -> None:
    quantity = "3 m at²"
    assert rewrite_constraints(f"The team measured {quantity}.").protected_tokens == (quantity,)
    assert numeric_table_value(quantity)
