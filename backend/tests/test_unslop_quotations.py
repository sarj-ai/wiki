import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import rewrite_constraints, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.unslop import scan_unslop_candidates
from tests.test_editorial_refinement import _RefiningEditor, _request


_QUOTATIONS = (
    "'we don't have enough data'",
    "‘we don’t have enough data’",
    "'we don’t have enough data'",
    "‘we don't have enough data’",
    "'the team's samples contain enough data'",
    "‘the team’s samples contain enough data’",
    "'l'équipe conserve assez de données'",
    "‘l’équipe conserve assez de données’",
)


@pytest.mark.parametrize("quotation", _QUOTATIONS)
def test_single_quoted_contractions_and_possessives_remain_complete(quotation: str) -> None:
    source = f"The reviewer retains {quotation} in the incident report."

    assert rewrite_constraints(source).quotations == (quotation,)


@pytest.mark.parametrize("quotation", _QUOTATIONS[:6])
def test_edits_inside_a_quotation_are_rejected_even_with_the_same_apostrophe(
    quotation: str,
) -> None:
    source = f"The reviewer retains {quotation} in the incident report."
    document = parse_markdown(source, title="Exact quotations")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [
            TextPatch(
                block_id=block.id,
                expected_sha256=block.sha256,
                replacement=source.replace("enough ", ""),
            )
        ],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["quoted_content_changed"]


@pytest.mark.parametrize("quotation", _QUOTATIONS)
def test_active_voice_can_improve_around_an_exact_single_quotation(quotation: str) -> None:
    source = f"The quotation {quotation} was retained by the reviewer in the incident report."
    replacement = f"The reviewer retained {quotation} in the incident report."
    document = parse_markdown(source, title="Active prose")
    block = document.blocks[0]
    decision = validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )

    assert decision.accepted == {block.id: replacement}
    assert not decision.findings


def test_apostrophes_do_not_merge_separate_quotations_or_mark_unquoted_contractions() -> None:
    source = "The team's record says 'we don't have data' and ‘the service isn’t ready’."

    assert rewrite_constraints(source).quotations == (
        "'we don't have data'",
        "‘the service isn’t ready’",
    )
    assert not rewrite_constraints("The team's record isn't ready.").quotations


@pytest.mark.parametrize(
    "quotation", ["'we don't delve into the topic'", "‘we don’t delve into the topic’"]
)
def test_scanner_masks_the_whole_quote_after_a_contraction(quotation: str) -> None:
    document = parse_markdown(f"The reviewer retains {quotation}.", title="Quoted wording")

    assert not scan_unslop_candidates(document)


@pytest.fixture
def quotation_editor() -> _RefiningEditor:
    return _RefiningEditor(("The reviewer said 'we don't have data' in the incident report.",))


async def test_quote_changes_never_reach_a_permissive_factual_reviewer(
    quotation_editor: _RefiningEditor,
) -> None:
    source = "The reviewer said 'we don't have enough data' in the incident report."
    original = parse_markdown(source, title="Exact quotations")

    outcome = await TextTransformPlanner(
        generator=quotation_editor, allow_unslop_model=True
    ).transform(original, _request(source))

    assert outcome.document == original
    assert not quotation_editor.sources
    assert any(finding.code == "quoted_content_changed" for finding in outcome.findings)


async def test_a_later_quote_change_keeps_the_previously_reviewed_active_draft(
    quotation_editor: _RefiningEditor,
) -> None:
    source = "The quotation 'we don't have enough data' was retained by the reviewer in the incident report."
    faithful = "The reviewer retained 'we don't have enough data' in the incident report."
    quotation_editor.drafts = (faithful, faithful.replace("enough ", ""))

    outcome = await TextTransformPlanner(
        generator=quotation_editor, allow_unslop_model=True
    ).transform(parse_markdown(source, title="Exact quotations"), _request(source))

    assert outcome.document.blocks[0].text == faithful
    assert quotation_editor.calls == 2
    assert quotation_editor.sources == [source]
    assert outcome.model_count == 1
    assert not outcome.findings
