from __future__ import annotations

from typing import Annotated, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field


class _PresentationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PresentationMutationPlan(_PresentationState):
    mutation_sha256: str = Field(min_length=1)
    base_revision_sha256: str = Field(min_length=1)
    expected_semantic_sha256: str = Field(min_length=1)
    expected_protected_structure_sha256: str = Field(min_length=1)
    expected_brand_sha256: Annotated[str, Field(min_length=1)] | None


class ReadyPresentation(_PresentationState):
    kind: Literal["ready"] = "ready"


class PendingPresentationMutation(_PresentationState):
    kind: Literal["pending"] = "pending"
    plan: PresentationMutationPlan


class AppliedPresentationMutation(_PresentationState):
    kind: Literal["applied"] = "applied"
    plan: PresentationMutationPlan


class RecordedPresentationMutation(_PresentationState):
    kind: Literal["recorded"] = "recorded"
    mutation_sha256: str = Field(min_length=1)


class CompletedPresentationMutation(_PresentationState):
    kind: Literal["completed"] = "completed"
    receipt: Annotated[
        AppliedPresentationMutation | RecordedPresentationMutation, Field(discriminator="kind")
    ]


type PresentationMutation = Annotated[
    ReadyPresentation
    | PendingPresentationMutation
    | AppliedPresentationMutation
    | RecordedPresentationMutation
    | CompletedPresentationMutation,
    Field(discriminator="kind"),
]


def confirmed_mutation(state: PresentationMutation) -> str | None:
    match state:
        case ReadyPresentation() | PendingPresentationMutation():
            return None
        case AppliedPresentationMutation(plan=plan):
            return plan.mutation_sha256
        case RecordedPresentationMutation(mutation_sha256=digest):
            return digest
        case CompletedPresentationMutation(receipt=receipt):
            return confirmed_mutation(receipt)
        case _:
            assert_never(state)


def pending_mutation_plan(state: PresentationMutation) -> PresentationMutationPlan | None:
    match state:
        case ReadyPresentation() | RecordedPresentationMutation():
            return None
        case PendingPresentationMutation(plan=plan) | AppliedPresentationMutation(plan=plan):
            return plan
        case CompletedPresentationMutation(receipt=receipt):
            return pending_mutation_plan(receipt)
        case _:
            assert_never(state)
