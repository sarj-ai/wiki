from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from app.document_transform.errors import InPlaceNeedsReviewError


class NativeChipNode(BaseModel):
    # This is a read-only view. Unknown Google properties stay in the original
    # payload, which is compared in full after restoring the planned spans.
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)
    document_id: str | None = Field(default=None, alias="documentId")
    revision_id: str | None = Field(default=None, alias="revisionId")
    title: str = ""
    start_index: int | None = Field(default=None, alias="startIndex")
    end_index: int | None = Field(default=None, alias="endIndex")
    tab_id: str | None = Field(default=None, alias="tabId")
    segment_id: str | None = Field(default=None, alias="segmentId")
    date_id: str | None = Field(default=None, alias="dateId")
    body: dict[str, JsonValue] = Field(default_factory=dict)
    named_ranges: dict[str, JsonValue] | list[JsonValue] = Field(
        default_factory=dict, alias="namedRanges"
    )
    paragraph_style: dict[str, JsonValue] = Field(default_factory=dict, alias="paragraphStyle")
    text_style: dict[str, JsonValue] = Field(default_factory=dict, alias="textStyle")
    date_properties: dict[str, JsonValue] = Field(
        default_factory=dict, alias="dateElementProperties"
    )
    ranges: list[JsonValue] = Field(default_factory=list)


def native_chip_node(value: dict[str, JsonValue]) -> NativeChipNode:
    try:
        validated = NativeChipNode.model_validate(value)
        # Preserve raw object identity for the reversible projection. Pydantic
        # validates strict field types first; no coerced values are substituted.
        return validated.model_copy(
            update={
                name: value[definition.alias or name]
                for name, definition in NativeChipNode.model_fields.items()
                if (definition.alias or name) in value
            }
        )
    except ValidationError as error:
        raise InPlaceNeedsReviewError from error
