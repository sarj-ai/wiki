import json
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class CustomHeaderTitleTarget(BaseModel):
    """One existing native header that Wiki may prefix without replacing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: DocsTabId
    header_id: DocsSegmentId
    terminal_newline_index: int | None = Field(default=None, ge=0)
    terminal_newline_text_style_json: str | None = Field(default=None, max_length=16_384)

    @model_validator(mode="after")
    def validate_terminal_newline_style(self) -> Self:
        if (self.terminal_newline_index is None) != (self.terminal_newline_text_style_json is None):
            message = "Terminal newline index and style must be captured together"
            raise ValueError(message)
        if self.terminal_newline_text_style_json is not None:
            try:
                style = json.loads(self.terminal_newline_text_style_json)
            except (json.JSONDecodeError, TypeError) as error:
                message = "Terminal newline style must be canonical JSON"
                raise ValueError(message) from error
            if (
                not isinstance(style, dict)
                or json.dumps(style, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                != self.terminal_newline_text_style_json
            ):
                message = "Terminal newline style must be a canonical JSON object"
                raise ValueError(message)
        return self


def custom_furniture_requests_safe(
    requests: list[dict[str, Any]],
    bindings: tuple[CustomFurnitureBinding, ...],
    *,
    allowed_header_requests: tuple[dict[str, Any], ...] = (),
) -> bool:
    if not bindings:
        return True
    tabs = {binding.tab_id for binding in bindings}
    header_tabs = {binding.tab_id for binding in bindings if binding.header_ids}
    segments = {
        identifier
        for binding in bindings
        for identifier in (*binding.header_ids, *binding.footer_ids)
    }
    observed_header_requests: list[dict[str, Any]] = []
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
        if not all(isinstance(value, dict) for value in request.values()):
            return False
        if "updateDocumentStyle" in request:
            tab_id = request["updateDocumentStyle"].get("tabId")
            if not isinstance(tab_id, str) or tab_id in tabs:
                return False
        if "createHeader" in request:
            location = request["createHeader"].get("sectionBreakLocation")
            if not isinstance(location, dict):
                return False
            tab_id = location.get("tabId")
            if not isinstance(tab_id, str) or tab_id in header_tabs:
                return False
        pending: list[object] = [request]
        targets_furniture = False
        while pending:
            match pending.pop():
                case dict() as value:
                    segment = value.get("segmentId")
                    if "segmentId" in value:
                        if not isinstance(segment, str):
                            return False
                        targets_furniture = targets_furniture or segment in segments
                    pending.extend(value.values())
                case list() as value:
                    pending.extend(value)
                case _:
                    pass
        if targets_furniture:
            observed_header_requests.append(request)
    return observed_header_requests == list(allowed_header_requests)
