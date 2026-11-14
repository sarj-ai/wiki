import json
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.document_transform.ir import MAX_DOC_TABS
from app.identifiers import DocsSegmentId, DocsTabId


MAX_FURNITURE_BYTES = 1_048_576


def furniture_context_values(value: object, keys: frozenset[str]) -> tuple[object, ...]:
    found: list[object] = []
    pending = [value]
    while pending:
        match pending.pop():
            case dict() as mapping:
                found.extend(child for key, child in mapping.items() if key in keys)
                pending.extend(reversed(tuple(mapping.values())))
            case list() as items:
                pending.extend(reversed(items))
            case _:
                pass
    return tuple(found)


def furniture_bindings_known(value: object) -> bool:
    return all(
        isinstance(binding, str) and bool(binding)
        for binding in furniture_context_values(
            value,
            frozenset(
                {
                    "defaultHeaderId",
                    "firstPageHeaderId",
                    "evenPageHeaderId",
                    "defaultFooterId",
                    "firstPageFooterId",
                    "evenPageFooterId",
                }
            ),
        )
    )


class CustomFurnitureBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: DocsTabId
    header_ids: tuple[DocsSegmentId, ...] = Field(max_length=MAX_DOC_TABS)
    footer_ids: tuple[DocsSegmentId, ...] = Field(max_length=MAX_DOC_TABS)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_bindings(self) -> Self:
        if (
            not self.tab_id
            or not (self.header_ids or self.footer_ids)
            or any(
                not all(values) or tuple(sorted(set(values))) != values
                for values in (self.header_ids, self.footer_ids)
            )
        ):
            message = "Furniture evidence requires unique, ordered, nonempty segment bindings"
            raise ValueError(message)
        return self


class CapturedTerminalNewline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int = Field(ge=0)
    text_style_json: str = Field(max_length=16_384)

    @model_validator(mode="after")
    def validate_terminal_newline_style(self) -> Self:
        try:
            style = json.loads(self.text_style_json)
        except (json.JSONDecodeError, TypeError) as error:
            message = "Terminal newline style must be canonical JSON"
            raise ValueError(message) from error
        if (
            not isinstance(style, dict)
            or json.dumps(style, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            != self.text_style_json
        ):
            message = "Terminal newline style must be a canonical JSON object"
            raise ValueError(message)
        return self


class CustomHeaderTitleTarget(BaseModel):
    """One existing native header that Wiki may prefix without replacing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: DocsTabId
    header_id: DocsSegmentId
    terminal_newline: CapturedTerminalNewline | None = None

    @model_validator(mode="before")
    @classmethod
    def decode_historical_newline_capture(cls, value: object) -> object:
        if not isinstance(value, dict) or {
            "terminal_newline_index",
            "terminal_newline_text_style_json",
        }.isdisjoint(value):
            return value
        if "terminal_newline" in value:
            message = "Conflicting terminal newline representations"
            raise ValueError(message)
        index = value.get("terminal_newline_index")
        style = value.get("terminal_newline_text_style_json")
        return {
            **{
                key: item
                for key, item in value.items()
                if key not in {"terminal_newline_index", "terminal_newline_text_style_json"}
            },
            "terminal_newline": None
            if index is None and style is None
            else {"index": index, "text_style_json": style},
        }


def custom_furniture_requests_safe(
    requests: list[dict[str, JsonValue]],
    bindings: tuple[CustomFurnitureBinding, ...],
    *,
    allowed_header_requests: tuple[dict[str, JsonValue], ...] = (),
) -> bool:
    if not bindings:
        return True
    segments = {
        identifier
        for binding in bindings
        for identifier in (*binding.header_ids, *binding.footer_ids)
    }
    observed_header_requests: list[dict[str, JsonValue]] = []
    for request in requests:
        if len(request) != 1 or not set(request) <= {
            "insertText",
            "deleteContentRange",
            "updateTextStyle",
            "updateParagraphStyle",
            "createParagraphBullets",
            "deleteParagraphBullets",
            "insertInlineImage",
            "createHeader",
            "updateDocumentStyle",
        }:
            return False
        ((command, body),) = request.items()
        if not isinstance(body, dict):
            return False
        if not _header_scope_safe(command, body, bindings):
            return False
        requested_segments = furniture_context_values(request, frozenset({"segmentId"}))
        if any(not isinstance(segment, str) for segment in requested_segments):
            return False
        if any(segment in segments for segment in requested_segments):
            observed_header_requests.append(request)
    return observed_header_requests == list(allowed_header_requests)


def _header_scope_safe(
    command: str,
    body: dict[str, JsonValue],
    bindings: tuple[CustomFurnitureBinding, ...],
) -> bool:
    if command == "updateDocumentStyle":
        tab_id = body.get("tabId")
        return isinstance(tab_id, str) and all(binding.tab_id != tab_id for binding in bindings)
    if command == "createHeader":
        location = body.get("sectionBreakLocation")
        if not isinstance(location, dict):
            return False
        tab_id = location.get("tabId")
        return isinstance(tab_id, str) and all(
            binding.tab_id != tab_id or not binding.header_ids for binding in bindings
        )
    return True
