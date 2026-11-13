from io import BytesIO

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
import pytest

from app.document_transform.docx_renderer import render_markdown_docx
from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.table_layout import numeric_columns, numeric_table_value
from tests.test_editorial_refinement import _RefiningEditor, _request


@pytest.mark.parametrize(
    ("literal", "altered"),
    [
        pytest.param("2 W/(m²·K)", "2 W/(m²·s)", id="denominator-unit"),
        pytest.param("2 W/(m²·K)", "2 W/(m³·K)", id="denominator-power"),
        pytest.param("2 W/(m²·K)", "2 W/(m²/K)", id="denominator-operator"),
        pytest.param("2 W/(m²·K)", "2 W/m²·K", id="removed-grouping"),
        pytest.param("5 J/(kg K)", "5 J/(kg s)", id="implicit-product"),
        pytest.param("3 kg/(m s²)", "3 kg/(m s³)", id="grouped-implicit-power"),
        pytest.param("7 mol/(m³·s)", "7 mol/(m³·h)", id="grouped-rate"),
        pytest.param("0.25 N/(m^2)", "0.25 N/(m^3)", id="ascii-power"),
        pytest.param("2 (m/s)²", "2 (m/s)³", id="group-power"),
        pytest.param("2 W / ( m² · K )", "2 W / ( m² / K )", id="authored-spacing"),
        pytest.param("2 W\u202f/\u202f(m²·K)", "2 W / (m²·K)", id="nonbreaking-spacing"),
    ],
)
def test_grouped_quantity_mutations_are_rejected_and_faithful_edits_remain_possible(
    literal: str, altered: str
) -> None:
    source = f"The reading was measured by the team at {literal}."
    document = parse_markdown(source, title="Compound measurements")
    block = document.blocks[0]

    assert literal in rewrite_constraints(source).protected_tokens
    rejected = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=f"The team measured the reading at {altered}.",
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert not rejected.accepted
    assert [finding.code for finding in rejected.findings] == ["protected_content_changed"]

    faithful = f"The team measured the reading at {literal}."
    accepted = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=faithful)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )
    assert accepted.accepted == {block.id: faithful}
    assert not accepted.findings


@pytest.fixture
def grouped_unit_editor() -> _RefiningEditor:
    return _RefiningEditor(("The team measured the reading at 2 W/(m²·s).",))


async def test_grouped_quantity_regression_never_reaches_a_permissive_factual_reviewer(
    grouped_unit_editor: _RefiningEditor,
) -> None:
    source = "The reading was measured by the team at 2 W/(m²·K)."
    original = parse_markdown(source, title="Exact dimensions")

    result = await TextTransformPlanner(
        generator=grouped_unit_editor, allow_unslop_model=True
    ).transform(original, _request(source))

    assert result.document == original
    assert not grouped_unit_editor.sources
    assert any(finding.code == "protected_content_changed" for finding in result.findings)


@pytest.mark.parametrize(
    "quantity",
    ["2 W/(m²·K)", "5 J/(kg K)", "3 kg/(m s²)", "7 mol/(m³·s)", "2 (m/s)²", "300 K"],
)
def test_grouped_measurements_align_numerically_without_changing_notation(quantity: str) -> None:
    rows = (("Measurement", "Value"), ("QA reading", quantity), ("Missing", "—"))
    assert numeric_table_value(quantity)
    assert numeric_columns(rows) == {1}

    source = f"| Measurement | Value |\n| --- | --- |\n| QA reading | {quantity} |\n| Missing | — |"
    output = Document(BytesIO(render_markdown_docx(source, title="Compound measurements")))

    assert output.tables[0].cell(1, 1).text == quantity
    assert output.tables[0].cell(1, 1).paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT


@pytest.mark.parametrize("text", ["2 W/(unknown)", "2 W/(m²·K", "2 W/(m²·K) extra"])
def test_incomplete_or_unknown_grouped_units_are_not_classified_as_numeric(text: str) -> None:
    assert not numeric_table_value(text)
