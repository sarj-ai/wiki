from collections.abc import Callable

import pytest

from app.document_transform.ir import parse_markdown
from app.document_transform.planner import TextTransformPlanner
from app.document_transform.safety import PatchDecision, validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from tests.test_editorial_refinement import _RefiningEditor, _request


def _decision(source: str, replacement: str) -> PatchDecision:
    document = parse_markdown(source, title="Date scope")
    block = document.blocks[0]
    return validate_patches(
        document,
        [TextPatch(block_id=block.id, expected_sha256=block.sha256, replacement=replacement)],
        phase=TransformPhase.UNSLOP,
        aggressive_unslop=True,
    )


@pytest.mark.parametrize("preposition", ["on", "by", "before", "after", "since", "until", "as of"])
def test_fronted_date_cannot_expand_to_an_unrelated_source_measurement(preposition: str) -> None:
    source = f"The calibration was recorded by the team at 1.234(5) ms {preposition} 2026-11-11. The interval was reported by the team as 12.3 ms ± 0.4 ms under load."
    replacement = f"{preposition.capitalize()} 2026-11-11, the team recorded calibration at 1.234(5) ms and reported an interval of 12.3 ms ± 0.4 ms under load."

    decision = _decision(source, replacement)

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["temporal_scope_changed"]


@pytest.mark.parametrize(
    "replacement",
    [
        "The team recorded calibration at 1.234(5) ms on 2026-11-11 and reported an interval of 12.3 ms ± 0.4 ms under load.",
        "The team recorded calibration at 1.234(5) ms on 2026-11-11, and reported an interval of 12.3 ms ± 0.4 ms under load.",
        "On 2026-11-11, the team recorded calibration at 1.234(5) ms. The team reported an interval of 12.3 ms ± 0.4 ms under load.",
        "On 2026-11-11, the team recorded calibration at 1.234(5) ms; the team reported an interval of 12.3 ms ± 0.4 ms under load.",
    ],
    ids=[
        "local-date",
        "local-date-with-comma",
        "separate-sentences",
        "separate-independent-clauses",
    ],
)
def test_active_voice_can_preserve_the_local_date_scope(replacement: str) -> None:
    source = "The calibration was recorded by the team at 1.234(5) ms on 2026-11-11. The interval was reported by the team as 12.3 ms ± 0.4 ms under load."

    assert _decision(source, replacement).accepted == {"b0": replacement}


def test_existing_shared_date_can_stay_shared_when_edited() -> None:
    source = "On 2026-11-11, the calibration was recorded by the team at 1.234(5) ms and the interval was reported by the team as 12.3 ms ± 0.4 ms under load."
    replacement = "On 2026-11-11, the team recorded calibration at 1.234(5) ms and reported an interval of 12.3 ms ± 0.4 ms under load."

    assert _decision(source, replacement).accepted == {"b0": replacement}


@pytest.mark.parametrize("current", ["", "current "])
def test_current_hardware_scope_cannot_disappear_in_the_live_pro_rewrite(current: str) -> None:
    source = (
        "The figures are rules of thumb rather than a new set of measurements of current hardware."
    )
    replacement = (
        f"The figures are rules of thumb rather than new measurements of {current}hardware."
    )

    decision = _decision(source, replacement)

    if current:
        assert decision.accepted == {"b0": replacement}
    else:
        assert not decision.accepted
        assert [finding.code for finding in decision.findings] == ["scope_qualifier_changed"]


@pytest.fixture
def temporal_editor_factory() -> Callable[[tuple[str, ...]], _RefiningEditor]:
    return _RefiningEditor


async def test_live_date_scope_expansion_never_reaches_a_permissive_reviewer(
    temporal_editor_factory: Callable[[tuple[str, ...]], _RefiningEditor],
) -> None:
    source = 'The calibration was recorded by the team at 1.234(5) ms on 2026-11-11. The interval was reported by the team as 12.3 ms ± 0.4 ms under load. These values are approximate and may vary. The team retains the exact quotation "do not round" and code literal `sample_rate=1e-6` with the citation [Google SRE](https://sre.google/books/).'
    replacement = 'On 2026-11-11, the team recorded 1.234(5) ms calibration and reported a 12.3 ms ± 0.4 ms interval under load (approximate values may vary), retaining "do not round", `sample_rate=1e-6` and [Google SRE](https://sre.google/books/).'
    original = parse_markdown(source, title="Live date scope")
    editor = temporal_editor_factory((replacement,))

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        original, _request(source)
    )

    assert outcome.document == original
    assert not editor.sources
    assert any(finding.code == "temporal_scope_changed" for finding in outcome.findings)


async def test_later_pass_keeps_original_date_scope_after_a_safe_sentence_merge(
    temporal_editor_factory: Callable[[tuple[str, ...]], _RefiningEditor],
) -> None:
    source = "The calibration was recorded by the team at 1.234(5) ms on 2026-11-11. The interval was reported by the team as 12.3 ms ± 0.4 ms under load."
    faithful = "On 2026-11-11, the team recorded calibration at 1.234(5) ms; the team reported an interval of 12.3 ms ± 0.4 ms under load."
    unsafe = "On 2026-11-11, the team recorded calibration at 1.234(5) ms and reported an interval of 12.3 ms ± 0.4 ms under load."
    editor = temporal_editor_factory((faithful, unsafe))

    outcome = await TextTransformPlanner(generator=editor, allow_unslop_model=True).transform(
        parse_markdown(source, title="Source-bound date scope"), _request(source)
    )

    assert editor.calls == 2
    assert outcome.document.blocks[0].text == faithful
    assert outcome.model_count == 1
    assert not outcome.findings


@pytest.mark.parametrize(
    ("original", "changed"),
    [
        ("before", "after"),
        ("after", "before"),
        ("by", "after"),
        ("since", "before"),
        ("until", "after"),
    ],
)
def test_temporal_operators_stay_bound_to_their_dates(original: str, changed: str) -> None:
    source = f"In order to recover, the worker must retry {original} 2026-11-01."
    replacement = f"To recover, the worker must retry {changed} 2026-11-01."

    assert [finding.code for finding in _decision(source, replacement).findings] == [
        "temporal_scope_changed"
    ]


def test_live_as_of_snapshot_does_not_become_an_on_date_assertion() -> None:
    source = "As of 2026-10-01, replay was not available. As of 2026-11-01, replay is available. The QA sample suggests replay could finish within 10–12 ms, except when a lease is stale. Correlation with fewer timeouts does not establish causation."
    replacement = "Replay was not available on 2026-10-01, but is available as of 2026-11-01. The QA sample suggests replay could finish within 10–12 ms, except for stale leases. Correlation with fewer timeouts does not establish causation."

    assert [finding.code for finding in _decision(source, replacement).findings] == [
        "temporal_scope_changed"
    ]


def test_temporal_operator_can_move_with_its_unchanged_date() -> None:
    source = "In order to recover, the worker must retry before 2026-11-01."
    replacement = "Before 2026-11-01, the worker must retry to recover."

    assert list(_decision(source, replacement).accepted.values()) == [replacement]


@pytest.mark.parametrize("ticks", ["`", "``"])
def test_code_formatted_dates_keep_their_temporal_operators(ticks: str) -> None:
    source = f"In order to recover, the worker must retry before {ticks}2026-11-01{ticks}."
    replacement = f"To recover, the worker must retry after {ticks}2026-11-01{ticks}."

    assert [finding.code for finding in _decision(source, replacement).findings] == [
        "temporal_scope_changed"
    ]


@pytest.mark.parametrize("separator", [" ", "   "])
def test_based_on_dated_measurements_can_use_active_voice(separator: str) -> None:
    source = f"The dashboard is based{separator}on 2026-11-01 measurements."
    replacement = "The dashboard uses 2026-11-01 measurements."

    assert list(_decision(source, replacement).accepted.values()) == [replacement]
