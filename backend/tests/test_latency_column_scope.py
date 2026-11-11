import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


@pytest.mark.parametrize(
    ("replacement", "code"),
    [
        (
            "Milliseconds are calculated unit conversions, including the first five source entries' blank columns.",
            "unit_column_identity_changed",
        ),
        (
            "The millisecond column gives calculated unit conversions, including the first five source entries' blank ms columns.",
            "source_entry_relationship_changed",
        ),
        (
            "The millisecond column gives calculated unit conversions, including the first five source blanks.",
            "source_entry_relationship_changed",
        ),
        (
            "The MS column gives calculated unit conversions, including the first five source entries, whose MS cells are blank.",
            "unit_column_identity_changed",
        ),
        (
            "The programs cells give calculated unit conversions, including the first five source entries, whose programs cells are blank.",
            "unit_column_identity_changed",
        ),
    ],
)
def test_inclusion_of_entries_cannot_become_inclusion_of_blank_columns(
    replacement: str, code: str
) -> None:
    source = "The millisecond column gives calculated unit conversions, including the first five entries for which the source leaves that column blank. This explanation repeats the column's purpose for clarity."
    document = parse_markdown(source, title="Column scope")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == [code]


@pytest.mark.parametrize(
    ("unit", "label"),
    [("nanosecond", "ns"), ("microsecond", "µs"), ("millisecond", "ms")],
)
def test_named_latency_fields_allow_abbreviations_with_explicit_cell_relationships(
    unit: str, label: str
) -> None:
    source = f"The {unit} column gives calculated unit conversions, including the first five entries for which the source leaves that column blank. This explanation repeats the column's purpose for clarity."
    replacement = f"The {label} column gives calculated unit conversions, including the first five source entries, whose {label} cells are blank."
    document = parse_markdown(source, title="Column scope")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}
