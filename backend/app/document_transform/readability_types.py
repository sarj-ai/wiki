from typing import Annotated, ClassVar, Literal, Self, TypeGuard, assert_never

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from app.identifiers import DocsRevisionId, DocsTabId


type ReadabilityVersion = Literal[1, 2, 3, 4, 5, 6]
READABILITY_PARAGRAPH_WORD_LIMIT = 45

CURRENT_READABILITY_VERSION: Literal[6] = 6

type ReadabilityStopReason = Literal[
    "unrecorded",
    "not_eligible",
    "converged",
    "no_improvement",
    "invalid_proposal",
    "provider_unavailable",
    "deadline_reserve",
    "pass_limit",
]


class _Refinement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class LegacyReadabilityRefinement(_Refinement):
    passes: int = Field(ge=0, le=100)
    stop_reason: Literal["unrecorded"] = "unrecorded"
    model_invoked: bool
    accepted_improvements: None = None
    planning_failure_code: str | None = Field(
        default=None, pattern=r"^model_[a-z_]+$", max_length=64
    )


class _RecordedRefinement(_Refinement):
    passes: int = Field(ge=0, le=100)
    accepted_improvements: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def bounded_improvements(self) -> Self:
        if self.accepted_improvements > self.passes:
            msg = "Accepted improvements exceed attempted passes"
            raise ValueError(msg)
        return self


class IneligibleReadabilityRefinement(_Refinement):
    stop_reason: Literal["not_eligible"] = "not_eligible"
    passes: Literal[0] = 0
    accepted_improvements: Literal[0] = 0
    planning_failure_code: ClassVar[None] = None


class FinishedReadabilityRefinement(_RecordedRefinement):
    stop_reason: Literal["converged", "no_improvement", "pass_limit"]
    passes: int = Field(ge=1, le=100)
    planning_failure_code: ClassVar[None] = None


class DeadlineReadabilityRefinement(_RecordedRefinement):
    stop_reason: Literal["deadline_reserve"] = "deadline_reserve"
    planning_failure_code: ClassVar[Literal["model_deadline_exhausted"]] = (
        "model_deadline_exhausted"
    )


class InvalidReadabilityRefinement(_RecordedRefinement):
    stop_reason: Literal["invalid_proposal"] = "invalid_proposal"
    passes: int = Field(ge=1, le=100)
    planning_failure_code: ClassVar[Literal["model_output_invalid"]] = "model_output_invalid"


class UnavailableReadabilityRefinement(_RecordedRefinement):
    stop_reason: Literal["provider_unavailable"] = "provider_unavailable"
    passes: int = Field(ge=1, le=100)
    planning_failure_code: str = Field(pattern=r"^model_[a-z_]+$", max_length=64)

    @model_validator(mode="after")
    def distinct_deadline(self) -> Self:
        if self.planning_failure_code == "model_deadline_exhausted":
            msg = "Provider deadline requires the deadline refinement variant"
            raise ValueError(msg)
        return self


type RecordedReadabilityRefinement = (
    IneligibleReadabilityRefinement
    | FinishedReadabilityRefinement
    | DeadlineReadabilityRefinement
    | InvalidReadabilityRefinement
    | UnavailableReadabilityRefinement
)
type ReadabilityRefinement = Annotated[
    LegacyReadabilityRefinement | RecordedReadabilityRefinement,
    Field(discriminator="stop_reason"),
]


type ReadabilityReceiptValue = (
    str
    | int
    | float
    | bool
    | list[ReadabilityReceiptValue]
    | dict[str, ReadabilityReceiptValue]
    | tuple[ReadabilityReceiptValue, ...]
    | frozenset[int]
    | None
)


class _SerializedReadabilityReceipt(RootModel[dict[str, ReadabilityReceiptValue]]):
    # model_dump's Python mode retains tuples and frozen numeric column sets.
    model_config = ConfigDict(strict=True)


_REFINEMENT_FIELDS = frozenset(
    {"model_invoked", "passes", "accepted_improvements", "stop_reason", "planning_failure_code"}
)


def _record(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


class _ReadabilityRefinementWire(BaseModel):
    # Historical flat receipt only; convert to the authoritative union before use.
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)
    model_invoked: bool = False
    passes: int = Field(default=0, ge=0, le=100)
    accepted_improvements: int | None = Field(default=None, ge=0, le=100)
    stop_reason: ReadabilityStopReason = "unrecorded"
    planning_failure_code: str | None = Field(
        default=None, pattern=r"^model_[a-z_]+$", max_length=64
    )

    def outcome(self) -> ReadabilityRefinement:
        if self.stop_reason == "unrecorded":
            if self.accepted_improvements is not None:
                msg = "Unrecorded refinement cannot claim a known improvement count"
                raise ValueError(msg)
            return LegacyReadabilityRefinement(
                passes=self.passes,
                model_invoked=self.model_invoked,
                planning_failure_code=self.planning_failure_code,
            )
        if self.accepted_improvements is None:
            msg = "Recorded refinement requires an improvement count"
            raise ValueError(msg)
        match self.stop_reason:
            case "not_eligible":
                refinement: RecordedReadabilityRefinement = IneligibleReadabilityRefinement()
                if self.passes or self.accepted_improvements:
                    msg = "Ineligible refinement cannot claim attempted passes"
                    raise ValueError(msg)
            case "converged" | "no_improvement" | "pass_limit":
                refinement = FinishedReadabilityRefinement(
                    stop_reason=self.stop_reason,
                    passes=self.passes,
                    accepted_improvements=self.accepted_improvements,
                )
            case "deadline_reserve":
                refinement = DeadlineReadabilityRefinement(
                    passes=self.passes, accepted_improvements=self.accepted_improvements
                )
            case "invalid_proposal":
                refinement = InvalidReadabilityRefinement(
                    passes=self.passes, accepted_improvements=self.accepted_improvements
                )
            case "provider_unavailable":
                if self.planning_failure_code is None:
                    msg = "Unavailable refinement requires a provider failure code"
                    raise ValueError(msg)
                refinement = UnavailableReadabilityRefinement(
                    passes=self.passes,
                    accepted_improvements=self.accepted_improvements,
                    planning_failure_code=self.planning_failure_code,
                )
            case _:
                assert_never(self.stop_reason)
        if self.planning_failure_code != refinement.planning_failure_code:
            msg = "Readability failure code contradicts its stop reason"
            raise ValueError(msg)
        if self.model_invoked != bool(refinement.passes):
            msg = "Readability model invocation contradicts its attempted passes"
            raise ValueError(msg)
        return refinement


class ReadabilityPart(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=20000)
    role: Literal["paragraph", "bullet", "numbered", "heading2", "heading3"]


class ReadabilityProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parts: tuple[ReadabilityPart, ...] = Field(min_length=1, max_length=32)


class ReadabilityEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[ReadabilityProposal] = Field(max_length=100)


class ReadabilityParagraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: DocsTabId
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    source: dict[str, JsonValue]
    text_style: dict[str, JsonValue]
    parts: tuple[ReadabilityPart, ...]


class ReadabilityTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: DocsTabId
    start: int = Field(ge=0)
    widths: tuple[int, ...]
    numeric: frozenset[int]
    header: bool = False
    # Older checkpoints formatted only the first row, even with native headers.
    header_rows: int = Field(default=1, ge=1)
    # Older checkpoints styled cells only when a header was detected.
    style_cells: bool = False


class ReadabilityPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Missing versions retain legacy grouped-list and horizontal-padding writes.
    version: ReadabilityVersion = 2
    source_revision_id: DocsRevisionId
    source_sha256: str
    invariant_sha256: str
    paragraphs: tuple[ReadabilityParagraph, ...] = ()
    tables: tuple[ReadabilityTable, ...] = ()
    original_lists: dict[str, dict[str, JsonValue]]
    requests: list[dict[str, JsonValue]]
    # One authoritative model. Computed fields preserve the encrypted receipt wire format.
    refinement: ReadabilityRefinement = Field(exclude=True)

    @model_validator(mode="before")
    @classmethod
    def decode_refinement(cls, value: object) -> object:
        if not _record(value):
            return value
        if "refinement" in value:
            if _REFINEMENT_FIELDS.intersection(value):
                msg = "Readability receipt supplies competing refinement representations"
                raise ValueError(msg)
            return value
        return {
            **{key: item for key, item in value.items() if key not in _REFINEMENT_FIELDS},
            "refinement": _ReadabilityRefinementWire.model_validate(value).outcome(),
        }

    @model_serializer(mode="wrap")
    def receipt(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, ReadabilityReceiptValue]:
        source = _SerializedReadabilityReceipt.model_validate(handler(self)).root
        projection = _ReadabilityRefinementWire(
            model_invoked=self.model_invoked,
            passes=self.passes,
            accepted_improvements=self.accepted_improvements,
            stop_reason=self.stop_reason,
            planning_failure_code=self.planning_failure_code,
        )
        include = (
            None
            if info.include is None
            else {name for name in _REFINEMENT_FIELDS if name in info.include}
        )
        # Pydantic's runtime selectors admit True/Ellipsis, omitted by its Info stub.
        raw_exclude: object = info.exclude
        exclude: set[str] = set()
        match raw_exclude:
            case dict():
                for name in _REFINEMENT_FIELDS:
                    decision: object = raw_exclude.get(name)
                    if decision is True or decision is Ellipsis:
                        exclude.add(name)
            case set() | frozenset():
                exclude = {name for name in _REFINEMENT_FIELDS if name in raw_exclude}
            case _:
                pass
        metadata = _SerializedReadabilityReceipt.model_validate(
            projection.model_dump(
                mode=info.mode,
                include=include,
                exclude=exclude,
                exclude_none=info.exclude_none,
                exclude_unset=info.exclude_unset,
                exclude_defaults=info.exclude_defaults,
                by_alias=info.by_alias,
                round_trip=info.round_trip,
            )
        ).root
        return {**source, **metadata}

    @property
    def model_invoked(self) -> bool:
        if isinstance(self.refinement, LegacyReadabilityRefinement):
            return self.refinement.model_invoked
        return bool(self.refinement.passes)

    @property
    def passes(self) -> int:
        return self.refinement.passes

    @property
    def accepted_improvements(self) -> int | None:
        return self.refinement.accepted_improvements

    @property
    def stop_reason(self) -> ReadabilityStopReason:
        return self.refinement.stop_reason

    @property
    def planning_failure_code(self) -> str | None:
        return self.refinement.planning_failure_code


class PlannedReadabilityCheckpoint(ReadabilityPlan):
    complete: Literal[False] = False
    revision_id: None = None


class CompletedReadabilityCheckpoint(ReadabilityPlan):
    complete: Literal[True] = True
    revision_id: DocsRevisionId


ReadabilityCheckpoint = Annotated[
    PlannedReadabilityCheckpoint | CompletedReadabilityCheckpoint, Field(discriminator="complete")
]


def complete_readability(
    checkpoint: ReadabilityCheckpoint, revision_id: DocsRevisionId
) -> CompletedReadabilityCheckpoint:
    return CompletedReadabilityCheckpoint.model_validate(
        {**checkpoint.model_dump(), "complete": True, "revision_id": revision_id}
    )
