from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Annotated, Literal, Self, assert_never, overload

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.document_transform.errors import IncompatiblePendingOutputError


type DraftDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _DraftState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class UnmodifiedDraft(_DraftState):
    kind: Literal["unmodified"] = "unmodified"


class RecordedDraftMutation(_DraftState):
    kind: Literal["recorded"] = "recorded"
    sha256: DraftDigest


class PendingDraftMutation(_DraftState):
    kind: Literal["pending"] = "pending"
    sha256: DraftDigest
    base_revision_sha256: DraftDigest
    previous: RecordedDraftMutation | None = None

    @model_validator(mode="after")
    def require_unacknowledged_intent(self) -> Self:
        if self.previous is not None and self.previous.sha256 == self.sha256:
            msg = "An acknowledged mutation requires the applied state"
            raise ValueError(msg)
        return self


class AppliedDraftMutation(_DraftState):
    kind: Literal["applied"] = "applied"
    sha256: DraftDigest
    base_revision_sha256: DraftDigest


type DraftMutation = Annotated[
    UnmodifiedDraft | RecordedDraftMutation | PendingDraftMutation | AppliedDraftMutation,
    Field(discriminator="kind"),
]


class IncompleteDraft(_DraftState):
    kind: Literal["incomplete"] = "incomplete"


class CompletedDraft(_DraftState):
    kind: Literal["complete"] = "complete"
    result_sha256: DraftDigest
    verified_revision_sha256: DraftDigest


type DraftCompletion = Annotated[IncompleteDraft | CompletedDraft, Field(discriminator="kind")]


def confirmed_draft_mutation(state: DraftMutation) -> str | None:
    match state:
        case UnmodifiedDraft():
            return None
        case RecordedDraftMutation(sha256=digest) | AppliedDraftMutation(sha256=digest):
            return digest
        case PendingDraftMutation(previous=receipt):
            return receipt.sha256 if receipt is not None else None
        case _:
            assert_never(state)


@overload
def record_draft_mutation(
    state: UnmodifiedDraft | RecordedDraftMutation, digest: str
) -> RecordedDraftMutation: ...


@overload
def record_draft_mutation(
    state: PendingDraftMutation | AppliedDraftMutation, digest: str
) -> AppliedDraftMutation: ...


def record_draft_mutation(
    state: DraftMutation, digest: str
) -> RecordedDraftMutation | AppliedDraftMutation:
    match state:
        case PendingDraftMutation():
            if state.sha256 != digest:
                raise IncompatiblePendingOutputError
            return AppliedDraftMutation(
                sha256=state.sha256,
                base_revision_sha256=state.base_revision_sha256,
            )
        case AppliedDraftMutation():
            if state.sha256 != digest:
                raise IncompatiblePendingOutputError
            return state
        case UnmodifiedDraft() | RecordedDraftMutation():
            return RecordedDraftMutation(sha256=digest)
        case _:
            assert_never(state)


@overload
def prepare_draft_mutation(
    state: UnmodifiedDraft, digest: str, base_revision_sha256: str
) -> PendingDraftMutation: ...


@overload
def prepare_draft_mutation(
    state: RecordedDraftMutation, digest: str, base_revision_sha256: str
) -> PendingDraftMutation | RecordedDraftMutation: ...


@overload
def prepare_draft_mutation(
    state: PendingDraftMutation, digest: str, base_revision_sha256: str
) -> PendingDraftMutation: ...


@overload
def prepare_draft_mutation(
    state: AppliedDraftMutation, digest: str, base_revision_sha256: str
) -> AppliedDraftMutation: ...


def prepare_draft_mutation(
    state: DraftMutation, digest: str, base_revision_sha256: str
) -> PendingDraftMutation | AppliedDraftMutation | RecordedDraftMutation:
    match state:
        case PendingDraftMutation() | AppliedDraftMutation():
            if state.sha256 != digest or state.base_revision_sha256 != base_revision_sha256:
                raise IncompatiblePendingOutputError
            return state
        case UnmodifiedDraft() | RecordedDraftMutation():
            if isinstance(state, RecordedDraftMutation) and state.sha256 == digest:
                return state
            return PendingDraftMutation(
                sha256=digest,
                base_revision_sha256=base_revision_sha256,
                previous=state if isinstance(state, RecordedDraftMutation) else None,
            )
        case _:
            assert_never(state)


def decode_draft_mutation(properties: Mapping[str, JsonValue]) -> DraftMutation:
    applied = _property(properties, "sarjTransformMutation")
    pending = _property(properties, "sarjTransformPendingMutation")
    base = _property(properties, "sarjTransformMutationBaseHash")
    receipt = RecordedDraftMutation(sha256=applied) if applied is not None else None
    if pending is None and base is None:
        return receipt if receipt is not None else UnmodifiedDraft()
    if pending is None or base is None:
        raise IncompatiblePendingOutputError
    if applied == pending:
        return AppliedDraftMutation(sha256=pending, base_revision_sha256=base)
    return PendingDraftMutation(sha256=pending, base_revision_sha256=base, previous=receipt)


def decode_draft_completion(properties: Mapping[str, JsonValue]) -> DraftCompletion:
    state = _property(properties, "sarjTransformState")
    result = _property(properties, "sarjTransformResultHash")
    revision = _property(properties, "sarjTransformVerifiedRevisionHash")
    match state:
        case "incomplete" if result is None and revision is None:
            return IncompleteDraft()
        case "complete" if result is not None and revision is not None:
            return CompletedDraft(result_sha256=result, verified_revision_sha256=revision)
        case _:
            raise IncompatiblePendingOutputError


def _property(properties: Mapping[str, JsonValue], key: str) -> str | None:
    if key not in properties:
        return None
    value = properties[key]
    if not isinstance(value, str) or not value:
        raise IncompatiblePendingOutputError
    if key != "sarjTransformState" and re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise IncompatiblePendingOutputError
    return value
