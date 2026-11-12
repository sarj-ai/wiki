from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.identifiers import DocsRevisionId, DocsTabId


type ReadabilityVersion = Literal[1, 2, 3, 4, 5]

CURRENT_READABILITY_VERSION: Literal[5] = 5

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
    model_invoked: bool = False
    passes: int = Field(default=0, ge=0, le=100)
    accepted_improvements: int | None = Field(default=None, ge=0, le=100)
    stop_reason: ReadabilityStopReason = "unrecorded"
    planning_failure_code: str | None = Field(
        default=None, pattern=r"^model_[a-z_]+$", max_length=64
    )


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
