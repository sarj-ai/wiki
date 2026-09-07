from dataclasses import replace

import pytest

from app.document_transform import executor
from app.document_transform.ir import parse_markdown
from app.document_transform.schemas import DocumentTransformRequest, MarkdownSource, TransformMode
from app.identifiers import FileId


@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
@pytest.mark.parametrize(
    "title",
    [
        "review",
        "Review — Sarj formatted",
        "Review — SARJ POLISHED",
        "Review — Sarj Polished — Sarj formatted",
        "Review 2026-09-05",
        "Review — Sarj polished draft",
        "x" * 255,
    ],
)
def test_automatic_copy_names_are_bounded_idempotent_and_preserve_source(
    title: str, mode: TransformMode
) -> None:
    original = parse_markdown("# authored heading\n\nBody", title=title)
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown",
            content="# authored heading\n\nBody",
            name=title,
            destination_folder_id=FileId("folder"),
        ),
        mode=mode,
    )
    named = executor._copy_output_name(request, original)
    assert executor._copy_output_name(request, replace(original, title=named)) == named
    assert len(named) <= 255
    assert original.title == title
    assert [block.text for block in original.blocks] == ["authored heading", "", "Body"]
    assert named.endswith(
        " — Sarj Formatted" if mode is TransformMode.FORMAT else " — Sarj Polished"
    )
    if title == "Review 2026-09-05":
        assert named.startswith("Review —")
        assert "2026-09-05" not in named
    elif title == "Review — Sarj polished draft":
        assert named.startswith("Review — Sarj Polished Draft")
    elif title != "x" * 255:
        assert named.startswith("Review — Sarj ")
        assert named.count(" — Sarj ") == 1


def test_explicit_copy_name_is_not_stripped_or_given_another_suffix() -> None:
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", content="Body", name="Source", destination_folder_id=FileId("folder")
        ),
        mode=TransformMode.BOTH,
        output_name="my 2026 review — Sarj formatted",
    )
    assert (
        executor._copy_output_name(request, parse_markdown("Body", title="Source"))
        == "My 2026 Review — Sarj Formatted"
    )


def test_mode_change_replaces_only_exact_generated_terminal_suffixes() -> None:
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", content="Body", name="Source", destination_folder_id=FileId("folder")
        ),
        mode=TransformMode.FORMAT,
    )
    assert (
        executor._copy_output_name(request, parse_markdown("Body", title="Review — Sarj polished"))
        == "Review — Sarj Formatted"
    )
    assert (
        executor._copy_output_name(
            request.model_copy(update={"mode": TransformMode.UNSLOP}),
            parse_markdown("Body", title="Review — Sarj polished"),
        )
        == "Review — Sarj polished — Unslopped"
    )


def test_historical_suffix_chains_remain_bounded_and_stable_after_truncation() -> None:
    request = DocumentTransformRequest(
        source=MarkdownSource(
            kind="markdown", content="Body", name="Source", destination_folder_id=FileId("folder")
        ),
        mode=TransformMode.FORMAT,
    )
    original = parse_markdown("Body", title="Review" + " — Sarj polished" * 40)
    named = executor._copy_output_name(request, original)
    assert len(named) <= 255
    assert executor._copy_output_name(request, replace(original, title=named)) == named
