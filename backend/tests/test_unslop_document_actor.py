from collections.abc import Callable

import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.unslop import reassigned_document_writing_action
from tests.test_editorial_refinement import _RefiningEditor, _request


@pytest.mark.parametrize(
    ("base", "active", "participle"),
    [
        pytest.param("bring together", "brings together", "Bringing together", id="bring-together"),
        pytest.param("collect", "collects", "Collecting", id="collect"),
        pytest.param("summarize", "summarizes", "Summarizing", id="summarize"),
        pytest.param("summarise", "summarises", "Summarising", id="summarise"),
        pytest.param("describe", "describes", "Describing", id="describe"),
        pytest.param("explain", "explains", "Explaining", id="explain"),
        pytest.param("present", "presents", "Presenting", id="present"),
        pytest.param("consolidate", "consolidates", "Consolidating", id="consolidate"),
        pytest.param("outline", "outlines", "Outlining", id="outline"),
        pytest.param("list", "lists", "Listing", id="list"),
        pytest.param("catalog", "catalogs", "Cataloging", id="catalog"),
        pytest.param("catalogue", "catalogues", "Cataloguing", id="catalogue"),
        pytest.param("cover", "covers", "Covering", id="cover"),
    ],
)
@pytest.mark.parametrize("actor", ["The team", "The service", "Atlas"])
@pytest.mark.parametrize("scaffold", [True, False], ids=["purpose-preamble", "active-document"])
def test_document_actions_cannot_move_to_a_concrete_actor_when_sentences_merge(
    base: str, active: str, participle: str, actor: str, scaffold: bool
) -> None:
    opening = f"This note is intended to {base}" if scaffold else f"This document {active}"
    source = f"{opening} latency estimates. {actor} measured 1 ms."
    document = parse_markdown(source, title="Actor preservation")
    block = document.blocks[0]
    replacements = (
        f"{actor} {active} latency estimates and measured 1 ms.",
        f"{participle} latency estimates, {actor.replace('The ', 'the ')} measured 1 ms.",
    )
    for replacement in replacements:
        decision = validate_patches(
            document,
            [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
            phase=TransformPhase.UNSLOP,
            aggressive_unslop=True,
        )
        assert not decision.accepted
        assert [finding.code for finding in decision.findings] == ["document_writing_actor_changed"]
    faithful = f"This document {active} latency estimates. {actor} measured 1 ms."
    if not scaffold:
        assert not reassigned_document_writing_action(source, faithful)
        return
    assert validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=faithful)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    ).accepted == {block.id: faithful}


def test_live_pro_repair_cannot_transfer_the_document_action_to_the_measuring_team() -> None:
    source = 'This working note is intended to bring together latency estimates from the Google SRE reference so the same information can be read in one place. The team measured 1.20e-6 s on 2026-11-10. These values are approximate and may vary under load. The team keeps the exact quotation "do not round" and the code literal `sample_rate=1e-6` with the citation [Google SRE](https://sre.google/books/).'
    replacement = 'Bringing together Google SRE latency estimates, the team measured 1.20e-6 s on 2026-11-10 (approximate, may vary under load), keeping `sample_rate=1e-6`, "do not round" and [Google SRE](https://sre.google/books/).'
    document = parse_markdown(source, title="Live actor regression")
    block = document.blocks[0]

    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["document_writing_actor_changed"]


@pytest.mark.parametrize(
    "span",
    [
        '"This note is intended to collect estimates"',
        "`This note is intended to collect estimates`",
        "[This note is intended to collect estimates](https://example.test/)",
    ],
    ids=["quotation", "code", "link-label"],
)
def test_quoted_code_and_linked_document_actions_are_not_actor_constraints(span: str) -> None:
    source = f"The team keeps {span} and measures latency."

    assert not reassigned_document_writing_action(source, source)


def test_an_existing_actor_action_still_requires_independent_factual_review() -> None:
    source = "This note is intended to collect latency estimates. The team collected field notes."

    assert not reassigned_document_writing_action(source, "The team collected field notes.")


@pytest.mark.parametrize(
    "action", ["Collating", "Compiling", "Gathering", "Combining", "Assembling"]
)
def test_paraphrasing_a_document_action_cannot_assign_it_to_the_measuring_team(action: str) -> None:
    source = "This note is intended to collect latency estimates. The team measured 1 ms."

    assert reassigned_document_writing_action(
        source, f"{action} latency estimates, the team measured 1 ms."
    )
    assert not reassigned_document_writing_action(
        source, "This document collects latency estimates. The team measured 1 ms."
    )


@pytest.fixture
def actor_editor_factory() -> Callable[[tuple[str, ...]], _RefiningEditor]:
    return _RefiningEditor


async def test_actor_transfer_never_reaches_even_a_permissive_factual_reviewer(
    actor_editor_factory: Callable[[tuple[str, ...]], _RefiningEditor],
) -> None:
    source = "This note is intended to collect latency estimates. The team measured 1 ms."
    original = parse_markdown(source, title="Actor preservation")
    editor = actor_editor_factory(("Collecting latency estimates, the team measured 1 ms.",))

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request(source)
    )

    assert outcome.document == original
    assert not editor.sources
    assert any(finding.code == "document_writing_actor_changed" for finding in outcome.findings)


async def test_later_pass_cannot_reassign_the_reviewed_drafts_document_action(
    actor_editor_factory: Callable[[tuple[str, ...]], _RefiningEditor],
) -> None:
    source = "This note is intended to collect latency estimates. The team measured 1 ms."
    faithful = "This document collects latency estimates. The team measured 1 ms."
    editor = actor_editor_factory(
        (faithful, "Collecting latency estimates, the team measured 1 ms.")
    )

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(source, title="Actor preservation"), _request(source)
    )

    assert editor.calls == 2
    assert outcome.document.blocks[0].text == faithful
    assert outcome.model_count == 1
    assert not outcome.findings
