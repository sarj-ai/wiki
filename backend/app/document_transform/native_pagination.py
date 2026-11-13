from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool

from app.document_transform.errors import InPlaceNeedsReviewError, OutputConflictError
from app.document_transform.ir import BlockKind, DocumentIR, TextBlock, utf16_length
from app.document_transform.native_terminal import plan_terminal_blank
from app.document_transform.readability_json import array, obj


MAX_PAGINATION_RULES = 60
_MAX_NATIVE_ELEMENTS = 2_000
_MAX_LEAD_CHARS = 120
_MAX_ITEM_CHARS = 240
_MAX_BODY_CHARS = 1_000
_MAX_BULLET_BYTES = 8_192
_MAX_SPACER_PROOF_BYTES = 8_192
_MAX_SPACER_SPACE_PT = 1_000
_KNOWN_NAMED_STYLES = frozenset(
    {"NORMAL_TEXT", "TITLE", "SUBTITLE", *(f"HEADING_{level}" for level in range(1, 7))}
)


class _ParagraphGrouping(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    keep_with_next: JsonValue = Field(default=None, alias="keepWithNext")
    keep_lines_together: JsonValue = Field(default=None, alias="keepLinesTogether")


class _PaginationStyle(_ParagraphGrouping):
    space_above: JsonValue = Field(default=None, alias="spaceAbove")
    space_below: JsonValue = Field(default=None, alias="spaceBelow")


class _PageBreakStyle(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    page_break_before: JsonValue = Field(default=False, alias="pageBreakBefore")


class _PageNamedStyle(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    paragraph_style: _PageBreakStyle = Field(
        default_factory=_PageBreakStyle, alias="paragraphStyle"
    )


class _PageNamedStyles(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    styles: list[_PageNamedStyle] = Field(default_factory=list, max_length=MAX_PAGINATION_RULES)


class _PageFormat(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    document_mode: str | None = Field(default=None, alias="documentMode")


class _PageDocumentStyle(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    document_format: _PageFormat = Field(default_factory=_PageFormat, alias="documentFormat")


class _PageScope(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    document_style: _PageDocumentStyle = Field(
        default_factory=_PageDocumentStyle, alias="documentStyle"
    )
    named_styles: _PageNamedStyles = Field(default_factory=_PageNamedStyles, alias="namedStyles")


class _StyledParagraph(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    paragraph_style: _PaginationStyle = Field(
        default_factory=_PaginationStyle, alias="paragraphStyle"
    )


class _StyledElement(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")

    start_index: int = Field(alias="startIndex")
    paragraph: _StyledParagraph


@dataclass(frozen=True, slots=True)
class _RuleStyleMatch:
    found: bool
    matches: bool


class NativePaginationRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(min_length=1, max_length=64)
    text_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    keep_with_next: StrictBool | None = None
    above_spacer_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    below_spacer_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")


def plan_native_pagination(
    native: dict[str, JsonValue] | None,
    document: DocumentIR,
    *,
    emit_diagnostics: bool = False,
    compact_terminal: bool = True,
) -> tuple[NativePaginationRule, ...]:
    diagnostics: dict[str, int | bool] = {}
    rules = _plan_native_pagination(
        native, document, diagnostics, compact_terminal=compact_terminal
    )
    if not emit_diagnostics:
        return rules
    logger.info(
        "document spacer eligibility diagnostics={diagnostics}",
        diagnostics={
            **diagnostics,
            "spacer_planned_above_count": sum(
                rule.above_spacer_sha256 is not None for rule in rules
            ),
            "spacer_planned_below_count": sum(
                rule.below_spacer_sha256 is not None for rule in rules
            ),
        },
    )
    return rules


def _plan_native_pagination(
    native: dict[str, JsonValue] | None,
    document: DocumentIR,
    diagnostics: dict[str, int | bool],
    *,
    compact_terminal: bool,
) -> tuple[NativePaginationRule, ...]:
    body = _pagination_body(native, document)
    if body is None or native is None:
        return ()
    content = array(obj(body["body"])["content"])
    located = [
        block
        for block in document.blocks
        if block.source_range is not None
        and block.source_range.segment_id is None
        and block.source_range.tab_id == document.tab_ids[0]
    ]
    by_start = {
        block.source_range.start_index: block for block in located if block.source_range is not None
    }
    if len(by_start) != len(located):
        return ()
    mapped = [_body_paragraph(element, by_start) for element in content]
    rules = _group_rules(mapped)
    if rules is None:
        return ()
    rules = _plan_spacers(
        native,
        body,
        content,
        mapped,
        tab_id=document.tab_ids[0],
        rules=rules,
        diagnostics=diagnostics,
    )
    if rules is None:
        return ()
    if compact_terminal:
        rules = _with_terminal_rule(native, document, rules)
    return tuple(rules[block.id] for block in document.blocks if block.id in rules)


def _plan_spacers(
    native: dict[str, JsonValue],
    body: dict[str, JsonValue],
    content: list[JsonValue],
    mapped: list[TextBlock | None],
    *,
    tab_id: str,
    rules: dict[str, NativePaginationRule],
    diagnostics: dict[str, int | bool],
) -> dict[str, NativePaginationRule] | None:
    diagnostics["spacer_scope_checked"] = True
    dependencies = _spacer_dependencies(content, mapped, tab_id)
    diagnostics["spacer_dependency_identity_known"] = dependencies is not None
    diagnostics["spacer_named_styles_known"] = _spacer_named_styles_known(native, body)
    styles_known = dependencies is not None and _spacer_dependencies_known(
        native, body, dependencies, diagnostics
    )
    diagnostics["spacer_used_named_styles_known"] = styles_known
    return _add_spacer_rules(content, mapped, tab_id, rules, diagnostics) if styles_known else rules


def _with_terminal_rule(
    native: dict[str, JsonValue], document: DocumentIR, rules: dict[str, NativePaginationRule]
) -> dict[str, NativePaginationRule]:
    terminal = plan_terminal_blank(native, document)
    if terminal is not None and (terminal.block_id in rules or len(rules) < MAX_PAGINATION_RULES):
        block = next(block for block in document.blocks if block.id == terminal.block_id)
        rule = rules.get(block.id) or NativePaginationRule(
            block_id=block.id, text_sha256=block.sha256
        )
        return {
            **rules,
            block.id: rule.model_copy(update={"below_spacer_sha256": terminal.spacer_sha256}),
        }
    return rules


def _pagination_body(
    native: dict[str, JsonValue] | None, document: DocumentIR
) -> dict[str, JsonValue] | None:
    # Never derive adjacency from flattened IR: it omits empty paragraphs and
    # section boundaries. Initial scope is a single flat native body only.
    if native is None or document.source_format != "google_doc" or len(document.tab_ids) != 1:
        return None
    tabs = native.get("tabs")
    if not isinstance(tabs, list) or len(tabs) != 1:
        return None
    tab = tabs[0]
    if not isinstance(tab, dict):
        return None
    properties = tab.get("tabProperties")
    if (
        tab.get("childTabs", []) != []
        or not isinstance(properties, dict)
        or properties.get("tabId") != document.tab_ids[0]
    ):
        return None
    body = tab.get("documentTab")
    if not isinstance(body, dict) or not _page_boundaries_known(native, body):
        return None
    native_body = body.get("body")
    if not isinstance(native_body, dict):
        return None
    content = native_body.get("content")
    if not isinstance(content, list) or len(content) > _MAX_NATIVE_ELEMENTS:
        return None
    return body


def _group_rules(mapped: list[TextBlock | None]) -> dict[str, NativePaginationRule] | None:
    rules: dict[str, NativePaginationRule] = {}
    for first, second, third in zip(mapped, mapped[1:], mapped[2:], strict=False):
        for block, keep in _pagination_group(first, second, third):
            rule = NativePaginationRule(
                block_id=block.id, text_sha256=block.sha256, keep_with_next=keep
            )
            if block.id in rules and rules[block.id] != rule:
                return None
            rules[block.id] = rule
        if len(rules) > MAX_PAGINATION_RULES:
            return None
    return rules


def _pagination_group(
    first: TextBlock | None, second: TextBlock | None, third: TextBlock | None
) -> tuple[tuple[TextBlock, bool], ...]:
    if first is None or second is None or third is None:
        return ()
    if not _contiguous(first, second) or not _contiguous(second, third):
        return ()
    if all(
        (
            first.kind is BlockKind.HEADING,
            len(first.text) <= _MAX_LEAD_CHARS,
            _is_group_lead(second),
            len(second.text) <= _MAX_LEAD_CHARS,
            third.kind is BlockKind.PARAGRAPH,
            not _is_group_lead(third),
            len(third.text) <= _MAX_BODY_CHARS,
        )
    ):
        return ((first, True), (second, True))
    if all(
        (
            first.kind is BlockKind.HEADING or _is_group_lead(first),
            len(first.text) <= _MAX_LEAD_CHARS,
            second.kind is BlockKind.LIST_ITEM,
            third.kind is BlockKind.LIST_ITEM,
            len(second.text) <= _MAX_ITEM_CHARS,
            len(third.text) <= _MAX_ITEM_CHARS,
            _same_list(second, third),
        )
    ):
        # Only the opening pair is linked. Item two explicitly ends it.
        return ((first, True), (second, True), (third, False))
    return ()


def _add_spacer_rules(
    content: list[Any],
    mapped: list[TextBlock | None],
    tab_id: str,
    rules: dict[str, NativePaginationRule],
    diagnostics: dict[str, int | bool],
) -> dict[str, NativePaginationRule] | None:
    planned_rules = dict(rules)
    for index in range(1, len(content) - 1):
        diagnostics["spacer_examined_count"] = diagnostics.get("spacer_examined_count", 0) + 1
        if not all(
            _plain_spacing_neighbor(item) for item in (content[index - 1], content[index + 1])
        ):
            diagnostics["spacer_neighbor_style_count"] = (
                diagnostics.get("spacer_neighbor_style_count", 0) + 1
            )
            continue
        first, third = mapped[index - 1], mapped[index + 1]
        spacer = _spacer_proof(content[index], first, third, tab_id, diagnostics=diagnostics)
        if spacer is None or first is None or third is None:
            continue
        for block, field in ((first, "below_spacer_sha256"), (third, "above_spacer_sha256")):
            existing = planned_rules.get(block.id) or NativePaginationRule(
                block_id=block.id, text_sha256=block.sha256
            )
            planned_rules[block.id] = existing.model_copy(update={field: spacer})
        if len(planned_rules) > MAX_PAGINATION_RULES:
            return None
    return planned_rules


def _plain_spacing_neighbor(element: object) -> bool:
    if not isinstance(element, dict):
        return False
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict):
        return False
    style = paragraph.get("paragraphStyle", {})
    return isinstance(style, dict) and not set(style) - {
        "namedStyleType",
        "headingId",
        "alignment",
        "direction",
        "lineSpacing",
        "spaceAbove",
        "spaceBelow",
        "keepWithNext",
        "keepLinesTogether",
        "avoidWidowAndOrphan",
        "pageBreakBefore",
    }


def _spacer_dependencies(
    content: list[Any], mapped: list[TextBlock | None], tab_id: str
) -> frozenset[str] | None:
    # Docs inheritance is corresponding named style -> NORMAL_TEXT. Include
    # both source and generated target roles; never infer adjacency from IR.
    dependencies = {"NORMAL_TEXT"}
    for index in range(1, len(content) - 1):
        first, third = mapped[index - 1], mapped[index + 1]
        if not all(
            _plain_spacing_neighbor(item) for item in (content[index - 1], content[index + 1])
        ):
            continue
        if _spacer_proof(content[index], first, third, tab_id) is None:
            continue
        for block in (first, third):
            if block is None or block.source_named_style not in _KNOWN_NAMED_STYLES:
                return None
            dependencies.add(block.source_named_style)
            if block.kind is BlockKind.HEADING and block.level in {1, 2, 3, 4, 5, 6}:
                dependencies.add(f"HEADING_{block.level}")
    return frozenset(dependencies)


def _spacer_dependencies_known(
    native: dict[str, Any],
    body: dict[str, Any],
    dependencies: frozenset[str],
    diagnostics: dict[str, int | bool],
) -> bool:
    for scope in (native, body):
        seen: set[str] = set()
        for entry in scope.get("namedStyles", {}).get("styles", []):
            name = entry.get("namedStyleType")
            if not isinstance(name, str) or name not in _KNOWN_NAMED_STYLES or name in seen:
                diagnostics["spacer_named_identity_count"] = 1
                return False
            seen.add(name)
            if name not in dependencies:
                diagnostics["spacer_unused_named_style_count"] = (
                    diagnostics.get("spacer_unused_named_style_count", 0) + 1
                )
                continue
            style = entry.get("paragraphStyle", {})
            if not _plain_spacing_neighbor({"paragraph": {"paragraphStyle": style}}):
                diagnostics["spacer_named_paragraph_fields_count"] = 1
                if isinstance(style, dict):
                    diagnostics["spacer_named_indent_present"] = any(
                        field in style for field in ("indentStart", "indentEnd", "indentFirstLine")
                    )
                    diagnostics["spacer_named_border_present"] = any(
                        field in style
                        for field in (
                            "borderTop",
                            "borderBottom",
                            "borderLeft",
                            "borderRight",
                            "borderBetween",
                        )
                    )
                    diagnostics["spacer_named_shading_present"] = "shading" in style
                    diagnostics.update(_named_style_value_diagnostics(style))
                return False
            if not _spacer_style_values_known(style):
                diagnostics["spacer_named_paragraph_values_count"] = 1
                return False
            text_style = entry.get("textStyle", {})
            if not isinstance(text_style, dict) or "link" in text_style:
                diagnostics["spacer_named_text_style_count"] = 1
                return False
    return True


def _named_style_value_diagnostics(style: dict[str, Any]) -> dict[str, bool]:
    # Describe only the first rejected dependency entry, never provider payloads.
    # Missing fields inherit: they are not explicit zero or transparent values.
    checks = {"spacer_named_values_first_entry_only": True}
    indents = [
        _dimension_state(style[field])
        for field in ("indentStart", "indentEnd", "indentFirstLine")
        if field in style
    ]
    if indents:
        checks["spacer_named_indent_schema_known"] = "unknown" not in indents
        if "unknown" not in indents:
            checks["spacer_named_indent_all_zero"] = all(value == "zero" for value in indents)
    if "shading" in style:
        shading = style["shading"]
        color = (
            _color_state(shading["backgroundColor"])
            if isinstance(shading, dict) and set(shading) == {"backgroundColor"}
            else "unknown"
        )
        checks["spacer_named_shading_schema_known"] = color != "unknown"
        if color != "unknown":
            checks["spacer_named_shading_explicit_transparent"] = color == "transparent"
    borders = [
        style[field]
        for field in ("borderTop", "borderBottom", "borderLeft", "borderRight", "borderBetween")
        if field in style
    ]
    if borders:
        known = all(_border_schema_known(border) for border in borders)
        checks["spacer_named_border_schema_known"] = known
        if known:
            checks["spacer_named_border_all_width_zero"] = all(
                _dimension_state(border["width"]) == "zero" for border in borders
            )
            checks["spacer_named_border_all_padding_zero"] = all(
                _dimension_state(border["padding"]) == "zero" for border in borders
            )
            checks["spacer_named_border_all_transparent"] = all(
                _color_state(border["color"]) == "transparent" for border in borders
            )
    return checks


def _dimension_state(value: object) -> Literal["zero", "nonzero", "unknown"]:
    if not isinstance(value, dict) or set(value) - {"magnitude", "unit"}:
        return "unknown"
    # Google serializes an accepted zero-point style as {"unit": "PT"}.
    # An absent magnitude is zero only with the explicit, supported unit.
    magnitude = value.get("magnitude", 0)
    # Bound before any float conversion; arbitrarily large JSON ints must not raise.
    if (
        not isinstance(magnitude, (int, float))
        or isinstance(magnitude, bool)
        or not -_MAX_SPACER_SPACE_PT <= magnitude <= _MAX_SPACER_SPACE_PT
        or value.get("unit") != "PT"
    ):
        return "unknown"
    return "zero" if magnitude == 0 else "nonzero"


def _color_state(value: object) -> Literal["transparent", "opaque", "unknown"]:
    if not isinstance(value, dict):
        return "unknown"
    if not value:
        return "transparent"
    if set(value) != {"color"}:
        return "unknown"
    color = value.get("color")
    if not isinstance(color, dict) or set(color) != {"rgbColor"}:
        return "unknown"
    rgb = color.get("rgbColor")
    if not isinstance(rgb, dict) or set(rgb) - {"red", "green", "blue"}:
        return "unknown"
    return (
        "opaque"
        if all(
            isinstance(component, (int, float))
            and not isinstance(component, bool)
            and 0 <= component <= 1
            for component in rgb.values()
        )
        else "unknown"
    )


def _border_schema_known(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    dash = value.get("dashStyle")
    return (
        set(value) == {"color", "width", "padding", "dashStyle"}
        and _color_state(value.get("color")) != "unknown"
        and _dimension_state(value.get("width")) != "unknown"
        and _dimension_state(value.get("padding")) != "unknown"
        and isinstance(dash, str)
        and dash in {"SOLID", "DOT", "DASH"}
    )


def _spacer_named_styles_known(native: dict[str, Any], body: dict[str, Any]) -> bool:
    # Preserve the original whole-document diagnostic meaning. Eligibility
    # separately proves only the actual candidate dependency closure.
    for scope in (native, body):
        for entry in scope.get("namedStyles", {}).get("styles", []):
            style = entry.get("paragraphStyle", {})
            if not _plain_spacing_neighbor({"paragraph": {"paragraphStyle": style}}):
                return False
            if not _spacer_style_values_known(style):
                return False
            text_style = entry.get("textStyle", {})
            if not isinstance(text_style, dict) or "link" in text_style:
                return False
    return True


def _spacer_proof(
    element: object,
    first: TextBlock | None,
    third: TextBlock | None,
    tab_id: str,
    *,
    diagnostics: dict[str, int | bool] | None = None,
) -> str | None:
    if (
        first is None
        or third is None
        or any(
            block.kind not in {BlockKind.PARAGRAPH, BlockKind.HEADING}
            or block.native_bullet_json is not None
            or block.source_style_text_sha256 != block.sha256
            for block in (first, third)
        )
    ):
        _spacer_declined(diagnostics, "spacer_neighbor_content_count")
        return None
    if first.source_range is None or third.source_range is None:
        _spacer_declined(diagnostics, "spacer_indices_count")
        return None
    if not isinstance(element, dict) or set(element) != {"startIndex", "endIndex", "paragraph"}:
        _spacer_declined(diagnostics, "spacer_container_count")
        return None
    start, end = element.get("startIndex"), element.get("endIndex")
    if (
        type(start) is not int
        or type(end) is not int
        or end != start + 1
        or first.source_range.end_index + 1 != start
        or third.source_range.start_index != end
    ):
        _spacer_declined(diagnostics, "spacer_indices_count")
        return None
    paragraph = element.get("paragraph")
    if not isinstance(paragraph, dict) or set(paragraph) - {"elements", "paragraphStyle"}:
        _spacer_declined(diagnostics, "spacer_container_count")
        return None
    style = paragraph.get("paragraphStyle", {})
    if (
        not isinstance(style, dict)
        or set(style)
        - {
            "namedStyleType",
            "spaceAbove",
            "spaceBelow",
            "lineSpacing",
            "keepWithNext",
            "keepLinesTogether",
            "avoidWidowAndOrphan",
            "alignment",
            "direction",
            "pageBreakBefore",
        }
        or style.get("namedStyleType", "NORMAL_TEXT") != "NORMAL_TEXT"
        or style.get("pageBreakBefore", False) is not False
        or not _spacer_style_values_known(style)
    ):
        _spacer_declined(diagnostics, "spacer_style_count")
        return None
    elements = paragraph.get("elements")
    if not isinstance(elements, list) or len(elements) != 1:
        _spacer_declined(diagnostics, "spacer_runs_count")
        return None
    run = elements[0]
    if not isinstance(run, dict) or set(run) - {"startIndex", "endIndex", "textRun"}:
        _spacer_declined(diagnostics, "spacer_runs_count")
        return None
    if (
        type(run.get("startIndex", start)) is not int
        or type(run.get("endIndex", end)) is not int
        or run.get("startIndex", start) != start
        or run.get("endIndex", end) != end
    ):
        _spacer_declined(diagnostics, "spacer_indices_count")
        return None
    text_run = run.get("textRun")
    if (
        not isinstance(text_run, dict)
        or set(text_run) - {"content", "textStyle"}
        or text_run.get("content") != "\n"
        or text_run.get("textStyle", {}) != {}
    ):
        _spacer_declined(diagnostics, "spacer_text_run_count")
        return None
    payload = json.dumps(
        {"tab_id": tab_id, "spacer": element}, sort_keys=True, separators=(",", ":")
    ).encode()
    if len(payload) > _MAX_SPACER_PROOF_BYTES:
        _spacer_declined(diagnostics, "spacer_budget_count")
        return None
    return hashlib.sha256(payload).hexdigest()


def _spacer_declined(
    diagnostics: dict[str, int | bool] | None,
    reason: Literal[
        "spacer_neighbor_content_count",
        "spacer_indices_count",
        "spacer_container_count",
        "spacer_style_count",
        "spacer_runs_count",
        "spacer_text_run_count",
        "spacer_budget_count",
    ],
) -> None:
    if diagnostics is not None:
        diagnostics[reason] = diagnostics.get(reason, 0) + 1


def _spacer_style_values_known(style: object) -> bool:
    if not isinstance(style, dict):
        return False
    for field in ("spaceAbove", "spaceBelow"):
        if field not in style:
            continue
        value = style.get(field)
        if not isinstance(value, dict) or set(value) != {"magnitude", "unit"}:
            return False
        magnitude = value.get("magnitude")
        if not isinstance(magnitude, (int, float)) or isinstance(magnitude, bool):
            return False
        if not math.isfinite(magnitude):
            return False
        if not 0 <= magnitude <= _MAX_SPACER_SPACE_PT or value.get("unit") != "PT":
            return False
    return all(
        type(style.get(field)) is bool
        for field in ("keepWithNext", "keepLinesTogether", "avoidWidowAndOrphan")
        if field in style
    )


def _page_boundaries_known(native: dict[str, JsonValue], body: dict[str, JsonValue]) -> bool:
    try:
        scopes = tuple(_PageScope.model_validate(scope) for scope in (native, body))
    except ValueError:
        return False
    return all(
        scope.document_style.document_format.document_mode in {None, "PAGES"}
        and all(
            style.paragraph_style.page_break_before is False for style in scope.named_styles.styles
        )
        for scope in scopes
    )


def _body_paragraph(element: object, by_start: dict[int, TextBlock]) -> TextBlock | None:
    if not isinstance(element, dict) or set(element) - {"startIndex", "endIndex", "paragraph"}:
        return None
    start, end = element.get("startIndex"), element.get("endIndex")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        return None
    paragraph = element.get("paragraph")
    block = by_start.get(start)
    if not isinstance(paragraph, dict) or set(paragraph) - {"elements", "paragraphStyle", "bullet"}:
        return None
    if block is None or not block.editable or block.native_content or not block.text.strip():
        return None
    if block.source_range is None or block.source_range.end_index != end - 1:
        return None
    style = paragraph.get("paragraphStyle", {})
    elements = paragraph.get("elements")
    if (
        not isinstance(style, dict)
        or style.get("pageBreakBefore", False) is not False
        or not isinstance(elements, list)
        or not elements
    ):
        return None
    if any(character in block.text for character in "\n\r\v\f\u2028\u2029"):
        return None
    raw_text = _plain_paragraph_text(elements)
    if raw_text is None:
        return None
    if (
        hashlib.sha256(raw_text.encode()).hexdigest() != block.source_style_text_sha256
        or utf16_length(raw_text) != end - start - 1
        or any(character in raw_text for character in "\n\r\v\f\u2028\u2029")
    ):
        return None
    return _with_existing_keep(block, style)


def _plain_paragraph_text(elements: list[Any]) -> str | None:
    runs: list[str] = []
    for item in elements:
        if not isinstance(item, dict) or set(item) - {"startIndex", "endIndex", "textRun"}:
            return None
        run = item.get("textRun")
        if not isinstance(run, dict):
            return None
        text = run.get("content")
        if not isinstance(text, str):
            return None
        runs.append(text)
    return "".join(runs).removesuffix("\n")


def _with_existing_keep(block: TextBlock, style: dict[str, JsonValue]) -> TextBlock:
    grouping = _ParagraphGrouping.model_validate(style)
    if grouping.keep_with_next is True and grouping.keep_lines_together is True:
        return replace(block, pagination_keep_with_next=True)
    return block


def _is_group_lead(block: TextBlock) -> bool:
    return block.is_authored_lead_in or (
        block.kind is BlockKind.PARAGRAPH and block.pagination_keep_with_next is True
    )


def _contiguous(first: TextBlock, second: TextBlock) -> bool:
    return bool(
        first.source_range is not None
        and second.source_range is not None
        and first.source_range.end_index + 1 == second.source_range.start_index
    )


def _same_list(first: TextBlock, second: TextBlock) -> bool:
    # The parser preserves complete bullet payloads, including list identity,
    # nesting and emphasis. Requiring equality is deliberately conservative.
    if (
        first.native_bullet_json is None
        or first.native_bullet_json != second.native_bullet_json
        or len(first.native_bullet_json) > _MAX_BULLET_BYTES
    ):
        return False
    try:
        bullet = json.loads(first.native_bullet_json)
    except ValueError:
        return False
    return (
        isinstance(bullet, dict)
        and isinstance(bullet.get("listId"), str)
        and bool(bullet["listId"])
        and type(bullet.get("nestingLevel", 0)) is int
        and bullet.get("nestingLevel", 0) >= 0
    )


def apply_native_pagination(
    document: DocumentIR, rules: tuple[NativePaginationRule, ...]
) -> DocumentIR:
    by_id = {rule.block_id: rule for rule in rules}
    if len(by_id) != len(rules) or set(by_id) - {block.id for block in document.blocks}:
        raise OutputConflictError
    blocks = []
    for block in document.blocks:
        rule = by_id.get(block.id)
        if rule is not None:
            if block.sha256 != rule.text_sha256:
                raise OutputConflictError
            blocks.append(
                replace(
                    block,
                    pagination_keep_with_next=rule.keep_with_next,
                    suppress_space_above=rule.above_spacer_sha256 is not None,
                    suppress_space_below=rule.below_spacer_sha256 is not None,
                )
            )
        else:
            blocks.append(block)
    return replace(document, blocks=tuple(blocks))


def existing_native_pagination(
    native: dict[str, JsonValue] | None, document: DocumentIR
) -> tuple[NativePaginationRule, ...]:
    # Recognize existing exact layout only; never plan new in-place groups.
    terminal = plan_terminal_blank(native, document)
    rules = plan_native_pagination(
        native, document, compact_terminal=terminal is not None and not terminal.needs_write
    )
    if not rules or native is None:
        return ()
    body = _pagination_body(native, document)
    if body is None:
        return ()
    styles = {}
    for value in array(obj(body["body"])["content"]):
        if isinstance(value, dict) and "paragraph" in value:
            element = value
            paragraph = _StyledElement.model_validate(element)
            styles[paragraph.start_index] = paragraph.paragraph.paragraph_style
    blocks = {block.id: block for block in document.blocks}
    found = False
    matches = True
    for rule in rules:
        block = blocks[rule.block_id]
        if block.source_range is None:
            raise InPlaceNeedsReviewError
        style = styles.get(block.source_range.start_index, _PaginationStyle())
        result = _existing_rule_style(rule, block, style)
        found = found or result.found
        matches = matches and result.matches
    if found and not matches:
        raise InPlaceNeedsReviewError
    return rules if found else ()


def _existing_rule_style(
    rule: NativePaginationRule, block: TextBlock, style: _PaginationStyle
) -> _RuleStyleMatch:
    found = False
    matches = True
    for digest, dimension in (
        (rule.above_spacer_sha256, style.space_above),
        (rule.below_spacer_sha256, style.space_below),
    ):
        if digest is not None:
            exact = _dimension_state(dimension) == "zero"
            found = found or exact
            matches = matches and exact
    if rule.keep_with_next is not None:
        exact = style.keep_with_next is rule.keep_with_next and style.keep_lines_together is True
        found = found or (exact and block.kind is not BlockKind.HEADING)
        matches = matches and exact
    return _RuleStyleMatch(found=found, matches=matches)
