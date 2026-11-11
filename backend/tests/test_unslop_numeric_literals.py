from collections.abc import Callable

import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from tests.test_editorial_refinement import _RefiningEditor, _request


@pytest.mark.parametrize(
    ("before", "after"),
    [
        pytest.param("1.20e-6 s", "1.20e+6 s", id="exponent-sign"),
        pytest.param("1.20e-6 s", "1.2e-6 s", id="significant-zeros"),
        pytest.param("1.234(5) ms", "1.234(5) s", id="parenthetical-uncertainty-unit"),
        pytest.param("1.234(5)", "1.234 5", id="detached-uncertainty"),
        pytest.param("1–2 ms", "1 2 ms", id="dropped-range-dash"),
        pytest.param("1 234 ms", "1  234 ms", id="grouped-number-literal"),
        pytest.param("1\u00a0234 ms", "1 234 ms", id="nonbreaking-group-separator"),
        pytest.param("1 1/2 h", "1 1 / 2 h", id="mixed-fraction-literal"),
        pytest.param(".5 s", "5 s", id="leading-decimal"),
        pytest.param(",5 s", "5 s", id="locale-leading-decimal"),
        pytest.param("-5 s", "5 s", id="negative-sign"),
        pytest.param("−5 s", "5 s", id="unicode-minus"),
        pytest.param("+5 s", "5 s", id="explicit-positive"),
        pytest.param("½", "¼", id="vulgar-fraction"),
        pytest.param("1½ h", "1¼ h", id="mixed-fraction"),
        pytest.param("1⁄4 s", "1⁄2 s", id="fraction-slash"),
        pytest.param("12.3 ± 0.4 ms", "12.3 ∓ 0.4 ms", id="uncertainty-operator"),
        pytest.param("12.3 ± 0.4 ms", "12.3 0.4 ms", id="dropped-uncertainty"),
        pytest.param("3 m²", "3 m³", id="unit-power"),
        pytest.param("3 m^2", "3 m^3", id="ascii-unit-power"),
        pytest.param("3 kg·m/s²", "3 kg·m/s³", id="compound-unit-power"),
        pytest.param("3 µs", "3 μs", id="unit-literal"),
        pytest.param("6.02×10²³", "6.02÷10²³", id="scientific-operator"),
        pytest.param("6.02×10²³", "6.02×10⁻²³", id="superscript-exponent-sign"),
        pytest.param("6.02 * 10^23", "6.02 / 10^23", id="ascii-scientific-operator"),
        pytest.param("($1,250.00)", "$1,250.00", id="accounting-negative"),
        pytest.param("-$1,250.00", "$1,250.00", id="signed-currency"),
        pytest.param("$-1,250.00", "$1,250.00", id="currency-signed-amount"),
        pytest.param("₹1,250.00", "₩1,250.00", id="currency-symbol"),
        pytest.param("≤5 ms", "≥5 ms", id="comparison-direction"),
        pytest.param("<5 ms", "5 ms", id="dropped-comparison"),
        pytest.param("1\u202f234.50 ms", "1234.5 ms", id="grouping-and-precision"),
        pytest.param("3‰", "3%", id="per-mille"),
        pytest.param("sensorA123", "sensorA124", id="numeric-identifier"),
        pytest.param("v1.2.3", "v1.2.4", id="version-literal"),
        pytest.param("2026-11-10", "2026-11-11", id="date"),
        pytest.param("1.20e-6 s and 1.20e-6 s", "1.20e-6 s", id="duplicate-quantity"),
    ],
)
def test_numeric_mutations_are_rejected_before_review_while_active_voice_is_allowed(
    before: str, after: str
) -> None:
    source = f"The reading was measured by the team at {before}."
    document = parse_markdown(source, title="Numeric preservation")
    block = document.blocks[0]
    unsafe = f"The team measured the reading at {after}."

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=unsafe)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["protected_content_changed"]
    faithful = f"The team measured the reading at {before}."
    assert validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=faithful)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    ).accepted == {block.id: faithful}


@pytest.mark.parametrize(
    "literal",
    [
        "1.20e-6 s",
        "½",
        "12.3 ± 0.4 ms",
        ".5 s",
        "-5 s",
        "3 m²",
        "6.02×10²³",
        "($1,250.00)",
        "1 234.50 ms",
        "1\u00a0234.50 ms",
        "1\u2009234.50 ms",
        "1\u202f234.50 ms",
        "1 234 567,80 EUR",
        "1 1/2 h",
        "-1 1⁄2 h",
        "1 1 / 2 h",
        "1 ½ h",
        "1.234(5) ms",
        "1.234(5)e-6 s",
        "6.67430(15) × 10^-11 m³/kg/s²",
        "1–2 ms",
    ],
)
def test_repair_constraints_include_the_complete_numeric_notation(literal: str) -> None:
    assert rewrite_constraints(f"The team measured {literal}.").protected_tokens == (literal,)


@pytest.fixture
def numeric_editor_factory() -> Callable[[tuple[str, ...]], _RefiningEditor]:
    return _RefiningEditor


async def test_changed_exponent_never_reaches_even_a_permissive_factual_reviewer(
    numeric_editor_factory: Callable[[tuple[str, ...]], _RefiningEditor],
) -> None:
    source = "The reading was measured by the team at 1.20e-6 s."
    original = parse_markdown(source, title="Numeric preservation")
    editor = numeric_editor_factory(("The team measured the reading at 1.20e+6 s.",))

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request(source)
    )

    assert outcome.document == original
    assert not editor.sources
    assert any(finding.code == "protected_content_changed" for finding in outcome.findings)
