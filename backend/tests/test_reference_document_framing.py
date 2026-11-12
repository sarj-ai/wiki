from collections.abc import Callable
from typing import override

import pytest

from app.document_transform.ir import DocumentIR, parse_markdown
from app.document_transform.models import PatchGenerator
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import (
    DocumentTransformRequest,
    TextPatch,
    TransformFinding,
    TransformPhase,
)
from app.document_transform.unslop import without_editorial_framing


_ORIGINAL = (
    "This working reference is intended to bring together the relay rules so the same "
    "information can be read in one place. All facts and values below are fictional QA examples."
)
_SEED = (
    "This reference is intended to bring together relay rules so the same information "
    "can be read in one place. All facts and values below are fictional QA examples."
)
_DIRECT = "This reference brings together relay rules. All facts and values below are fictional QA examples."


def _accepted(source: str, target: str) -> bool:
    document = parse_markdown(source, title="Relay QA reference")
    block = document.blocks[0]
    return bool(
        validate_patches(
            document,
            [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=target)],
            phase=TransformPhase.UNSLOP,
            aggressive_unslop=True,
        ).accepted
    )


@pytest.mark.parametrize("subject", ["This working reference", "This reference document"])
def test_explicit_document_reference_can_drop_its_writing_scaffolding(subject: str) -> None:
    assert _accepted(_ORIGINAL.replace("This working reference", subject), _DIRECT)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("This reference is intended to collect leases.", "This reference collects leases."),
        ("This reference may describe the cache.", "This reference describes the cache."),
        (
            (
                "This reference is intended to collect leases. "
                "This document is intended to bring together rules."
            ),
            ("This working reference collects leases. This document is intended to gather rules."),
        ),
        (
            "This working reference is intended to outlive the query.",
            "This reference outlives the query.",
        ),
        (_ORIGINAL + " The cache may be stale.", _DIRECT + " The cache is stale."),
        (
            _ORIGINAL + " Mira measured latency.",
            (
                "Mira brings together relay rules and measured latency. "
                "All facts and values below are fictional QA examples."
            ),
        ),
        ('The quote is "' + _ORIGINAL + '"', 'The quote is "' + _DIRECT + '"'),
        ("`" + _ORIGINAL + "`", "`" + _DIRECT + "`"),
    ],
)
def test_reference_exception_keeps_goals_uncertainty_actor_and_literals(
    source: str, target: str
) -> None:
    assert not _accepted(source, target)


def test_reference_mask_keeps_name_quantity_and_unicode_offsets() -> None:
    source = (
        "This working reference is intended to describe 東京. "
        "Mira may use 1.2300 ms. The quote is “don’t retry”."
    )

    masked = without_editorial_framing(source)

    assert len(masked) == len(source)
    assert len(masked.encode("utf-16-le")) == len(source.encode("utf-16-le"))
    for literal in ["東京", "Mira", "may", "1.2300 ms", "“don’t retry”"]:
        assert masked.index(literal) == source.index(literal)
    assert "intended" not in masked


@pytest.mark.parametrize(
    ("left", "right"),
    [("`alpha`", "`beta`"), ("`the alpha`", "`alpha the`"), ("`a b`", "`a  b`")],
)
def test_reference_identity_preserves_order_and_exact_protected_literal_content(
    left: str, right: str
) -> None:
    source = f"This working reference is intended to describe {left} rather than {right}."

    assert _accepted(source, f"This reference describes {left} rather than {right}.")
    assert not _accepted(source, f"This reference describes {right} rather than {left}.")


@pytest.mark.parametrize("literal", ["1.2300 ms", "`alpha. beta`", '"alpha. beta"'])
def test_reference_sentence_boundary_keeps_decimal_code_and_quote_contents(literal: str) -> None:
    source = f"This working reference is intended to describe {literal}. The cache may be stale."
    target = f"This reference describes {literal}. The cache may be stale."

    assert _accepted(source, target)


def test_unknown_reference_predicate_cannot_acquire_a_writing_exception() -> None:
    source = (
        "This working reference is intended to outlive the query. "
        "I hope this helps. I enjoy an introduction within an introduction."
    )
    bad = "This reference is intended to collect leases. The text is intended to outlive the query."

    assert not _accepted(source, bad)


def test_quote_final_sentence_period_preserves_the_exact_following_tail() -> None:
    source = (
        'This working reference is intended to describe "the cache." '
        "Mira duplicates records so the same information can be read in one place."
    )
    faithful = (
        'This reference describes "the cache." '
        "Mira duplicates records so the same information can be read in one place."
    )

    assert _accepted(source, faithful)
    assert not _accepted(source, 'This reference describes "the cache." Mira duplicates records.')


def test_compound_reference_sentence_keeps_another_actors_purpose() -> None:
    source = (
        'This working reference is intended to describe "the cache"; '
        "Mira duplicates records so the same information can be read in one place."
    )
    bad = 'This reference describes "the cache"; Mira duplicates records.'

    assert not _accepted(source, bad)


def test_reference_provenance_is_bound_to_the_original_block() -> None:
    technical = "This reference is intended to collect leases."
    original = parse_markdown(_ORIGINAL + "\n\n" + technical, title="QA reference")
    current = original.replace_blocks({original.blocks[0].id: _SEED})
    block = next(block for block in current.blocks if block.text == technical)

    decision = validate_patches(
        current,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement="This reference collects leases.",
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
        framing_source=original,
    )

    assert not decision.accepted
    assert "force_language_changed" in [finding.code for finding in decision.findings]


def test_reference_actor_inside_code_still_makes_document_identity_ambiguous() -> None:
    source = (
        "This working reference is intended to bring together relay rules. "
        "The `reference` is intended to collect leases."
    )
    bad = (
        "This reference collects leases; it is the `reference`. "
        "This document is intended to gather relay rules."
    )

    assert not _accepted(source, bad)


@pytest.mark.parametrize(
    ("constraint", "moved_constraint"),
    [
        ("is intended to", "The text is intended to state relay rules."),
        ("can", "The text can be read in one place."),
    ],
)
def test_reference_frame_cannot_absorb_another_actors_goal_or_capability(
    constraint: str, moved_constraint: str
) -> None:
    source = _ORIGINAL + f" The `C++` pointer {constraint} collect leases."
    bad = (
        "This reference collects leases; it is the `C++` pointer. "
        + moved_constraint
        + " All facts and values below are fictional QA examples."
    )

    assert not _accepted(source, bad)


@pytest.mark.parametrize("technical_subject", ["reference", "pointer"])
@pytest.mark.parametrize("use_seed", [False, True])
def test_reordered_technical_reference_cannot_inherit_document_identity(
    technical_subject: str,
    use_seed: bool,
) -> None:
    source = _ORIGINAL + f" The `C++` {technical_subject} is intended to collect leases."
    seed = (
        "This reference is intended to collect leases; it is the `C++` reference. "
        "This document is intended to gather relay rules. "
        "All facts and values below are fictional QA examples."
    )
    original = parse_markdown(source, title="QA reference")
    current = original.replace_blocks({original.blocks[0].id: seed}) if use_seed else original
    block = current.blocks[0]
    bad = seed.replace("This reference is intended to collect", "This reference collects")

    decision = validate_patches(
        current,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=bad)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
        framing_source=original,
    )

    assert not decision.accepted
    assert not {finding.code for finding in decision.findings}.isdisjoint(
        {"force_language_changed", "document_reference_identity_ambiguous"}
    )


class _ReferenceEditor(PatchGenerator):
    supports_semantic_rewrite = True

    @property
    @override
    def model_ids(self) -> tuple[str, ...]:
        return ()

    def __init__(self, steps: tuple[str, ...]) -> None:
        self.steps = steps
        self.generated_sources: list[str] = []
        self.review_sources: list[str] = []
        self.repair_calls = 0

    @override
    async def generate(self, document: DocumentIR, *, phase: TransformPhase) -> list[TextPatch]:
        block = document.blocks[0]
        self.generated_sources.append(block.text)
        if len(self.generated_sources) > len(self.steps):
            return []
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=self.steps[len(self.generated_sources) - 1],
            )
        ]

    @override
    async def verify(self, document: DocumentIR, patches: list[TextPatch]) -> frozenset[str]:
        self.review_sources.append(document.blocks[0].text)
        return frozenset(patch.block_id for patch in patches)

    @override
    async def repair(
        self,
        document: DocumentIR,
        patches: list[TextPatch],
        findings: tuple[TransformFinding, ...],
    ) -> list[TextPatch]:
        self.repair_calls += 1
        block = document.blocks[0]
        return [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=block.text + " The cache will be stale.",
            )
        ]


@pytest.fixture
def reference_editor_factory() -> Callable[[tuple[str, ...]], _ReferenceEditor]:
    return _ReferenceEditor


async def test_refinement_keeps_original_reference_identity_and_the_verified_best(
    reference_editor_factory: Callable[[tuple[str, ...]], _ReferenceEditor],
) -> None:
    facts = " The cache may be stale. The worker must not retry rejected writes."
    source = _ORIGINAL + facts
    seed, direct = _SEED + facts, _DIRECT + facts
    original = parse_markdown(source, title="Relay QA reference")
    editor = reference_editor_factory((seed, direct))
    request = DocumentTransformRequest.model_validate(
        {
            "source": {
                "kind": "markdown",
                "content": source,
                "name": "QA reference",
                "destination_folder_id": "qa-only",
            },
            "mode": "unslop",
        }
    )

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, request
    )

    assert outcome.document.blocks[0].text == direct
    assert editor.generated_sources == [source, seed, direct]
    assert editor.review_sources == [source, source]
    assert editor.repair_calls > 0
    assert outcome.model_count == 2
    assert "force_language_changed" not in [finding.code for finding in outcome.findings]


async def test_planner_retains_source_when_reference_identity_becomes_ambiguous(
    reference_editor_factory: Callable[[tuple[str, ...]], _ReferenceEditor],
) -> None:
    source = _ORIGINAL + " The `C++` pointer is intended to collect leases."
    bad = (
        "This reference collects leases; it is the `C++` reference. "
        "This document is intended to gather relay rules. "
        "All facts and values below are fictional QA examples."
    )
    original = parse_markdown(source, title="Relay QA reference")
    editor = reference_editor_factory((bad,))
    request = DocumentTransformRequest.model_validate(
        {
            "source": {
                "kind": "markdown",
                "content": source,
                "name": "QA reference",
                "destination_folder_id": "qa-only",
            },
            "mode": "unslop",
        }
    )

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, request
    )

    assert outcome.document.text_sha256 == original.text_sha256
    assert outcome.model_count == 0
