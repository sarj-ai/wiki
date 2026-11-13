from pydantic import JsonValue, ValidationError
import pytest

from app.document_transform.copy_draft_state import (
    CompletedDraft,
    IncompleteDraft,
    PendingDraftMutation,
    RecordedDraftMutation,
    UnmodifiedDraft,
    confirmed_draft_mutation,
    decode_draft_completion,
    decode_draft_mutation,
    prepare_draft_mutation,
    record_draft_mutation,
)
from app.document_transform.errors import IncompatiblePendingOutputError


def test_pending_copy_preserves_the_previous_durable_receipt() -> None:
    previous = RecordedDraftMutation(sha256="a" * 64)
    pending = prepare_draft_mutation(previous, "b" * 64, "c" * 64)
    assert confirmed_draft_mutation(pending) == "a" * 64
    assert prepare_draft_mutation(pending, "b" * 64, "c" * 64) == pending
    recorded = record_draft_mutation(pending, "b" * 64)
    assert isinstance(recorded, PendingDraftMutation)
    assert recorded.sha256 == pending.sha256
    assert recorded.base_revision_sha256 == pending.base_revision_sha256
    assert confirmed_draft_mutation(recorded) == "b" * 64
    assert record_draft_mutation(recorded, "b" * 64) == recorded
    with pytest.raises(IncompatiblePendingOutputError):
        prepare_draft_mutation(pending, "d" * 64, "c" * 64)
    with pytest.raises(IncompatiblePendingOutputError):
        prepare_draft_mutation(pending, "b" * 64, "d" * 64)
    with pytest.raises(IncompatiblePendingOutputError):
        record_draft_mutation(pending, "d" * 64)


def test_copy_without_mutation_and_copy_with_a_recorded_noop_are_distinct() -> None:
    initial = decode_draft_mutation({})
    assert isinstance(initial, UnmodifiedDraft)
    assert confirmed_draft_mutation(initial) is None
    assert record_draft_mutation(initial, "a" * 64) == RecordedDraftMutation(sha256="a" * 64)


def test_current_provider_properties_decode_without_rewriting_audit_evidence() -> None:
    properties = {
        "sarjTransformMutation": "a" * 64,
        "sarjTransformPendingMutation": "b" * 64,
        "sarjTransformMutationBaseHash": "c" * 64,
    }
    before = dict(properties)
    state = decode_draft_mutation(properties)
    assert state == PendingDraftMutation(
        sha256="b" * 64,
        base_revision_sha256="c" * 64,
        previous=RecordedDraftMutation(sha256="a" * 64),
    )
    assert properties == before


@pytest.mark.parametrize(
    "properties",
    [
        {"sarjTransformPendingMutation": "a" * 64},
        {"sarjTransformMutationBaseHash": "b" * 64},
        {"sarjTransformMutation": ""},
        {"sarjTransformMutation": "not-a-digest"},
        {"sarjTransformMutation": None},
        {"sarjTransformMutation": False},
    ],
    ids=["missing-base", "orphan-base", "empty", "malformed", "null", "boolean"],
)
def test_invalid_mutation_properties_cannot_become_a_fresh_copy(
    properties: dict[str, JsonValue],
) -> None:
    with pytest.raises(IncompatiblePendingOutputError):
        decode_draft_mutation(properties)


def test_completed_copy_requires_both_result_and_revision_evidence() -> None:
    assert decode_draft_completion({"sarjTransformState": "incomplete"}) == IncompleteDraft()
    assert decode_draft_completion(
        {
            "sarjTransformState": "complete",
            "sarjTransformResultHash": "a" * 64,
            "sarjTransformVerifiedRevisionHash": "b" * 64,
        }
    ) == CompletedDraft(result_sha256="a" * 64, verified_revision_sha256="b" * 64)
    with pytest.raises(ValidationError):
        CompletedDraft.model_validate({"kind": "complete", "result_sha256": "a" * 64})


@pytest.mark.parametrize(
    "properties",
    [
        {},
        {"sarjTransformState": "unknown"},
        {"sarjTransformState": "complete"},
        {"sarjTransformState": "complete", "sarjTransformResultHash": "a" * 64},
        {"sarjTransformState": "incomplete", "sarjTransformResultHash": "a" * 64},
        {"sarjTransformState": "complete", "sarjTransformVerifiedRevisionHash": "b" * 64},
    ],
    ids=[
        "missing",
        "unknown",
        "unproven-completion",
        "missing-revision",
        "contradictory",
        "missing-result",
    ],
)
def test_partial_completion_is_held_instead_of_replayed(properties: dict[str, JsonValue]) -> None:
    with pytest.raises(IncompatiblePendingOutputError):
        decode_draft_completion(properties)
