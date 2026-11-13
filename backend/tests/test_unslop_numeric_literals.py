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
        pytest.param("20 ℃", "20 ℉", id="celsius-symbol-unit"),
        pytest.param("20 ℉", "20 ℃", id="fahrenheit-symbol-unit"),
        pytest.param("20 ℃", "20", id="dropped-celsius-unit"),
        pytest.param("20 ° C", "20 ° F", id="spaced-temperature-unit"),
        pytest.param("20 ° c", "20 ° f", id="authored-lowercase-temperature-unit"),
        pytest.param("293 K", "293", id="dropped-kelvin-symbol"),
        pytest.param("20 °\u202fC", "20 °\u202fF", id="narrow-space-temperature-unit"),
        pytest.param("20 ° C", "20 °C", id="temperature-unit-spacing"),
        pytest.param("20 ℃/s", "20 ℉/s", id="temperature-rate-unit"),
        pytest.param("3 W/℃", "3 W/℉", id="temperature-denominator-unit"),
        pytest.param("20 ℃²", "20 ℉²", id="temperature-unit-power"),
        pytest.param("3 W/° C", "3 W/° F", id="spaced-temperature-denominator"),
        pytest.param("3 W/(m ° C)", "3 W/(m ° F)", id="grouped-temperature-denominator"),
        pytest.param("3 W m⁻¹ K⁻¹", "3 W m⁻¹ C⁻¹", id="kelvin-product-unit"),
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
        pytest.param("9.81 m / s²", "9.81 m / s³", id="spaced-quotient-power"),
        pytest.param("3 kg · m / s²", "3 kg · m / s³", id="spaced-product-power"),
        pytest.param("3 kg * m / s²", "3 kg * m / s³", id="spaced-ascii-product"),
        pytest.param("9.81 m / s²", "9.81 m * s²", id="spaced-unit-operator"),
        pytest.param("9.81 m / s²", "9.81 m / h²", id="spaced-denominator-unit"),
        pytest.param("3 m ^ 2", "3 m ^ 3", id="spaced-unit-power"),
        pytest.param("3 m ** 2", "3 m ** 3", id="spaced-double-star-power"),
        pytest.param("3 kg m s⁻²", "3 kg m s⁻³", id="implicit-product-power"),
        pytest.param("9.81 m s^-2", "9.81 m h^-2", id="implicit-denominator-unit"),
        pytest.param("12.3 ms ± 0.4 ms", "12.3 ms ± 0.4 s", id="repeated-unit-uncertainty"),
        pytest.param("12.3 ms ± 0.4 ms", "12.3 ms + 0.4 ms", id="repeated-unit-operator"),
        pytest.param("1.2 x 10^3 s", "1.2 + 10^3 s", id="ascii-scientific-multiplication"),
        pytest.param("12.3   ms", "12.3 ms", id="multiple-unit-separators"),
        pytest.param("3 µs", "3 μs", id="unit-literal"),
        pytest.param("6.02×10²³", "6.02÷10²³", id="scientific-operator"),
        pytest.param("6.02×10²³", "6.02×10⁻²³", id="superscript-exponent-sign"),
        pytest.param("6.02 * 10^23", "6.02 / 10^23", id="ascii-scientific-operator"),
        pytest.param("3 · 10⁻⁶ s", "3 10⁻⁶ s", id="dropped-middle-dot"),
        pytest.param("3·10⁻⁶ s", "3 10⁻⁶ s", id="unspaced-middle-dot"),
        pytest.param("3 · 10⁻⁶ s", "3 × 10⁻⁶ s", id="changed-middle-dot"),
        pytest.param("2 · 3 ms", "2 3 ms", id="middle-dot-product"),
        pytest.param("($1,250.00)", "$1,250.00", id="accounting-negative"),
        pytest.param("-$1,250.00", "$1,250.00", id="signed-currency"),
        pytest.param("$-1,250.00", "$1,250.00", id="currency-signed-amount"),
        pytest.param("₹1,250.00", "₩1,250.00", id="currency-symbol"),
        pytest.param("≤5 ms", "≥5 ms", id="comparison-direction"),
        pytest.param("<5 ms", "5 ms", id="dropped-comparison"),
        pytest.param("<=5 ms", "<5 ms", id="inclusive-comparison"),
        pytest.param("1'234.50 ms", "1’234.50 ms", id="straight-group-separator"),
        pytest.param("1’234,50 ms", "1’234.50 ms", id="grouped-decimal-separator"),
        pytest.param("12.3 +/- 0.4 ms", "12.3 /- 0.4 ms", id="ascii-uncertainty-operator"),
        pytest.param("1\u202f234.50 ms", "1234.5 ms", id="grouping-and-precision"),
        pytest.param("١٢٫٣ ms", "١٢٬٣ ms", id="arabic-decimal-separator"),
        pytest.param("١٬٢٣٤٫٥٠ ms", "١٫٢٣٤٫٥٠ ms", id="arabic-group-separator"),
        pytest.param("12٫3 ms", "12٬3 ms", id="western-digits-arabic-separators"),
        pytest.param("۱۲٫۳۴۰۰ ms", "۱۲٬۳۴۰۰ ms", id="persian-digits-arabic-separators"),
        pytest.param("٫٥ ms", "٥ ms", id="arabic-leading-decimal"),
        pytest.param("١٢٫٣٪", "١٢٫٣", id="arabic-percent-sign"),
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
        "3 · 10⁻⁶ s",
        "3·10⁻⁶ s",
        "2 · 3 ms",
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
        "9.81 m / s²",
        "3 kg · m / s²",
        "3 kg * m / s²",
        "3 m ^ 2",
        "3 m ** 2",
        "3 m ^ -2",
        "9.81 m\u00a0/\u00a0s²",
        "9.81 m\u2009/\u2009s²",
        "9.81 m\u202f/\u202fs²",
        "9.81 m s^-2",
        "3 kg m s⁻²",
        "12.3 ms ± 0.4 ms",
        "1.2 x 10^3 s",
        "12.3   ms",
        "3 m⁻",
        "3 m²⁻",
        "1–2 ms",
        "١٬٢٣٤٫٥٠ ms",
        "12٬345٫6700 ms",
        "۱۲٫۳۴۰۰ ms",
        "٫٥ ms",
        "−١٢٫٣ ms",
        "١٫٢٣٤(٥) ms",
        "١٢٫٣٪",
    ],
)
def test_repair_constraints_include_the_complete_numeric_notation(literal: str) -> None:
    assert rewrite_constraints(f"The team measured {literal}.").protected_tokens == (literal,)


@pytest.mark.parametrize("prose", ["under load", "in May", "must remain exact", "now", "moving"])
def test_numeric_units_do_not_absorb_following_prose(prose: str) -> None:
    assert rewrite_constraints(f"The team measured 3 m / s² {prose}.").protected_tokens == (
        "3 m / s²",
    )


@pytest.mark.parametrize("conjunction", ["and", "or", "but", "nor", "yet"])
def test_numeric_literals_do_not_absorb_a_following_conjunction(conjunction: str) -> None:
    assert rewrite_constraints(
        f"The team recorded 2026-11-11 {conjunction} retained the report."
    ).protected_tokens == ("2026-11-11",)


@pytest.mark.parametrize("boundary", ["\n", "\r", "\v", "\f", "\u0085", "\u2028", "\u2029"])
def test_compound_units_cannot_cross_existing_line_boundaries(boundary: str) -> None:
    assert rewrite_constraints(f"The reading is 3 m{boundary}/ s².").protected_tokens == ("3 m",)


@pytest.fixture
def numeric_editor_factory() -> Callable[[tuple[str, ...]], _RefiningEditor]:
    return _RefiningEditor


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("20 ℃", "20 ℉"),
        ("20 ° C", "20 ° F"),
        ("1.20e-6 s", "1.20e+6 s"),
        ("9.81 m / s²", "9.81 m / s³"),
        ("3 kg m s⁻²", "3 kg m s⁻³"),
        ("12.3 ms ± 0.4 ms", "12.3 ms + 0.4 ms"),
        ("١٢٫٣ ms", "١٢٬٣ ms"),
        ("١٬٢٣٤٫٥٠ ms", "١٫٢٣٤٫٥٠ ms"),
        ("٫٥ ms", "٥ ms"),
        ("١٢٫٣٪", "١٢٫٣"),
        ("3 · 10⁻⁶ s", "3 10⁻⁶ s"),
    ],
)
async def test_changed_quantity_never_reaches_even_a_permissive_factual_reviewer(
    before: str,
    after: str,
    numeric_editor_factory: Callable[[tuple[str, ...]], _RefiningEditor],
) -> None:
    source = f"The reading was measured by the team at {before}."
    original = parse_markdown(source, title="Numeric preservation")
    editor = numeric_editor_factory((f"The team measured the reading at {after}.",))

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request(source)
    )

    assert outcome.document == original
    assert not editor.sources
    assert any(finding.code == "protected_content_changed" for finding in outcome.findings)
