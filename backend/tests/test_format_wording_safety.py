from typing import override

import pytest

from app.document_transform import models as models_module
from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.models import PatchGenerator
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    MarkdownSource,
    TextPatch,
    TransformMode,
    TransformPhase,
)
from app.identifiers import FileId


@pytest.mark.parametrize(
    "replacement",
    [
        "The service deletes backups.",
        "The service stores backups!",
        "the service stores backups.",
        "The  service stores backups.",
        "The service stores backups. ",
        "The service stores backups.\n",
        "The service stores\u00a0backups.",
        "",
        " ",
    ],
)
def test_format_rejects_every_wording_change(replacement: str) -> None:
    document = parse_markdown("The service stores backups.", title="Test")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.FORMAT,
    )
    assert decision.accepted == {}
    assert [finding.code for finding in decision.findings] == ["format_text_changed"]


def test_format_exact_identity_is_noop() -> None:
    document = parse_markdown("The service stores backups.", title="Test")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=block.text)],
        phase=TransformPhase.FORMAT,
    )
    assert decision.accepted == {}
    assert decision.findings == ()


@pytest.mark.parametrize(
    "guard", ["unknown_block", "stale_block", "protected_block", "duplicate_patch"]
)
def test_existing_identity_and_protection_guards_keep_priority(guard: str) -> None:
    document = parse_markdown(
        "```\nProtected\n```" if guard == "protected_block" else "Source", title="Test"
    )
    block = document.blocks[0]
    patch = TextPatch(
        block_id="unknown" if guard == "unknown_block" else block.id,
        expected_sha256="0" * 64 if guard == "stale_block" else block.sha256,
        replacement="Changed",
    )
    patches = [patch, patch] if guard == "duplicate_patch" else [patch]
    decision = validate_patches(document, patches, phase=TransformPhase.FORMAT)
    assert decision.accepted == {}
    assert decision.findings[-1].code == guard


class _UnsafeFormatGenerator(PatchGenerator):
    def __init__(self) -> None:
        self.calls: list[TransformPhase] = []

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ()

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        self.calls.append(phase)
        block = document.blocks[0]
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement="The service deletes backups.",
            )
        ]


@pytest.mark.parametrize("mode", [TransformMode.FORMAT, TransformMode.BOTH])
async def test_enabled_format_generator_cannot_change_text_or_undo_safe_unslop(
    mode: TransformMode,
) -> None:
    source = (
        "Moreover, the service stores backups."
        if mode is TransformMode.BOTH
        else "The service stores backups."
    )
    original = parse_markdown(source, title="Test")
    generator = _UnsafeFormatGenerator()
    result = await TextTransformPlanner(
        generator=generator, allow_format_text_edits=True
    ).transform(
        original,
        DocumentTransformRequest(
            source=MarkdownSource(
                kind="markdown", content=source, name="Test", destination_folder_id=FileId("folder")
            ),
            mode=mode,
        ),
    )
    assert result.document.blocks[0].text == "The service stores backups."
    assert result.applied_count == result.deterministic_count == int(mode is TransformMode.BOTH)
    assert result.model_count == 0
    assert generator.calls == [TransformPhase.FORMAT]
    assert [finding.code for finding in result.findings] == ["format_text_changed"]
    assert original.blocks[0].text == source


def test_format_prompt_requires_no_text_replacements() -> None:
    prompt = models_module._prompt(
        parse_markdown("Source", title="Test"), phase=TransformPhase.FORMAT
    )
    assert "Do not propose text replacements" in prompt
    assert 'Return {"patches":[]}' in prompt
    assert "Improve clarity" not in prompt
