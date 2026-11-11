import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        (
            "It should be noted that the estimates are approximate and are not service guarantees.",
            "The estimates are approximate and are not service guarantees.",
        ),
        (
            "The cache may be stale. It should be noted that operators must retain raw measurements.",
            "The cache may be stale. Operators must retain raw measurements.",
        ),
        (
            "It should be noted that the service is intended to reduce delays under load.",
            "The service is intended to reduce delays under load.",
        ),
    ],
)
def test_editorial_note_frame_does_not_force_a_rewrite_to_keep_throat_clearing(
    source: str, replacement: str
) -> None:
    document = parse_markdown(source, title="Measurements")
    block = document.blocks[0]

    result = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert result.accepted == {block.id: replacement}
    assert not result.findings
    assert (
        rewrite_constraints(source).force_language
        == rewrite_constraints(replacement).force_language
    )


@pytest.mark.parametrize(
    ("source", "replacement"),
    [
        (
            "It should be noted that operators must retain raw measurements.",
            "Operators retain raw measurements.",
        ),
        (
            "It should be noted that the cache may be stale.",
            "The cache is stale.",
        ),
        (
            "The reviewer said it should be noted that the estimates are approximate.",
            "The reviewer said the estimates are approximate.",
        ),
        (
            'The reviewer said, "It should be noted that the estimates are approximate."',
            'The reviewer said, "The estimates are approximate."',
        ),
        (
            "It should be noted before archiving that the samples were lost.",
            "The samples were lost.",
        ),
        (
            "It should be noted that the cache is stale. Reports should retain the note.",
            "The cache is stale. Reports retain the note.",
        ),
    ],
)
def test_note_frame_exception_keeps_real_force_and_reported_or_quoted_instructions(
    source: str, replacement: str
) -> None:
    document = parse_markdown(source, title="Measurements")
    block = document.blocks[0]

    result = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not result.accepted
    assert result.findings
