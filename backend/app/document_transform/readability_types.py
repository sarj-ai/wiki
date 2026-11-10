from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.identifiers import DocsRevisionId, DocsTabId


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


class ReadabilityCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    source_revision_id: DocsRevisionId
    source_sha256: str
    invariant_sha256: str
    paragraphs: tuple[ReadabilityParagraph, ...] = ()
    tables: tuple[ReadabilityTable, ...] = ()
    original_lists: dict[str, dict[str, JsonValue]]
    requests: list[dict[str, JsonValue]]
    complete: bool = False
    revision_id: DocsRevisionId | None = None
    model_invoked: bool = False
    passes: int = Field(default=0, ge=0, le=100)
