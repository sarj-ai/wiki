from copy import deepcopy
from dataclasses import dataclass, replace
import json
from typing import Any

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.ir import DocumentIR, parse_google_document


_RGB_SERIALIZATION_TOLERANCE = 0.000001


@dataclass(frozen=True)
class _StyleRange:
    tab_id: str
    start_index: int
    end_index: int


def align_branded_bullet_metadata(actual: DocumentIR, expected: DocumentIR) -> DocumentIR:
    if len(actual.blocks) != len(expected.blocks):
        return actual
    if all(
        observed.native_bullet_json == planned.native_bullet_json
        for observed, planned in zip(actual.blocks, expected.blocks, strict=True)
    ):
        return actual
    styles = _planned_styles(expected)
    blocks = []
    for observed, planned in zip(actual.blocks, expected.blocks, strict=True):
        source = planned.source_range
        if (
            source is None
            or not planned.editable
            or observed.native_bullet_json is None
            or planned.native_bullet_json is None
        ):
            blocks.append(observed)
            continue
        permitted = styles.get(_StyleRange(source.tab_id, source.start_index, source.end_index), {})
        original = json.loads(planned.native_bullet_json)
        current = json.loads(observed.native_bullet_json)
        if _only_planned_style_changes(original, current, permitted):
            if "textStyle" in original:
                current["textStyle"] = original["textStyle"]
            else:
                current.pop("textStyle", None)
            restored = json.dumps(current, sort_keys=True)
        else:
            restored = observed.native_bullet_json
        blocks.append(replace(observed, native_bullet_json=restored))
    return replace(actual, blocks=tuple(blocks))


def _planned_styles(expected: DocumentIR) -> dict[_StyleRange, dict[str, Any]]:
    requests = build_copy_mutations(
        expected, expected, profile=SarjBrandProfile(), headers=(), logo_uri=""
    )
    styles: dict[_StyleRange, dict[str, Any]] = {}
    for request in requests:
        update = request.get("updateTextStyle", {})
        scope = update.get("range", {})
        if set(scope) == {"tabId", "startIndex", "endIndex"}:
            key = _StyleRange(scope["tabId"], scope["startIndex"], scope["endIndex"])
            styles.setdefault(key, {}).update(update.get("textStyle", {}))
    return styles


def bullet_style_diagnostics(actual: DocumentIR, expected: DocumentIR) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(
        (
            "bullet_presence_mismatch",
            "bullet_identity_mismatch",
            "bullet_nesting_mismatch",
            "bullet_other_property_mismatch",
            "bullet_projection_accepted",
            "bullet_not_editable",
            "bullet_font_mismatch",
            "bullet_size_mismatch",
            "bullet_color_mismatch",
            "bullet_emphasis_mismatch",
            "bullet_link_mismatch",
            "bullet_unknown_style_mismatch",
        ),
        0,
    )
    counts.update(
        dict.fromkeys(
            (
                f"bullet_{field}_{dimension}"
                for field in ("bold", "italic", "underline")
                for dimension in (
                    "added_true",
                    "added_false",
                    "removed_true",
                    "removed_false",
                    "changed",
                    "request_present",
                    "request_expected_false",
                )
            ),
            0,
        )
    )
    styles = _planned_styles(expected)
    for observed, planned in zip(actual.blocks, expected.blocks, strict=False):
        if observed.native_bullet_json is None or planned.native_bullet_json is None:
            counts["bullet_presence_mismatch"] += int(
                observed.native_bullet_json != planned.native_bullet_json
            )
            continue
        before = json.loads(planned.native_bullet_json)
        after = json.loads(observed.native_bullet_json)
        counts["bullet_identity_mismatch"] += int(before.get("listId") != after.get("listId"))
        counts["bullet_nesting_mismatch"] += int(
            before.get("nestingLevel") != after.get("nestingLevel")
        )
        counts["bullet_other_property_mismatch"] += int(
            {
                key: value
                for key, value in before.items()
                if key not in {"listId", "nestingLevel", "textStyle"}
            }
            != {
                key: value
                for key, value in after.items()
                if key not in {"listId", "nestingLevel", "textStyle"}
            }
        )
        source = planned.source_range
        permitted = (
            styles.get(_StyleRange(source.tab_id, source.start_index, source.end_index), {})
            if source
            else {}
        )
        counts["bullet_not_editable"] += int(not planned.editable)
        counts["bullet_projection_accepted"] += int(
            planned.editable
            and before != after
            and _only_planned_style_changes(before, after, permitted)
        )
        before_style = before.get("textStyle", {})
        after_style = after.get("textStyle", {})
        if not isinstance(before_style, dict) or not isinstance(after_style, dict):
            counts["bullet_unknown_style_mismatch"] += 1
            continue
        for field in before_style.keys() | after_style.keys():
            if (
                field in before_style
                and field in after_style
                and before_style[field] == after_style[field]
            ):
                continue
            if (
                field in permitted
                and field in after_style
                and _style_value_matches(field, after_style[field], permitted[field])
            ):
                continue
            bucket = {
                "weightedFontFamily": "bullet_font_mismatch",
                "fontSize": "bullet_size_mismatch",
                "foregroundColor": "bullet_color_mismatch",
                "bold": "bullet_emphasis_mismatch",
                "italic": "bullet_emphasis_mismatch",
                "underline": "bullet_emphasis_mismatch",
                "link": "bullet_link_mismatch",
            }.get(field, "bullet_unknown_style_mismatch")
            counts[bucket] += 1
            if field in {"bold", "italic", "underline"}:
                counts[f"bullet_{field}_request_present"] += int(field in permitted)
                counts[f"bullet_{field}_request_expected_false"] += int(
                    permitted.get(field) is False
                )
                dimension = "changed"
                if field not in before_style and type(after_style.get(field)) is bool:
                    dimension = "added_true" if after_style[field] else "added_false"
                elif field not in after_style and type(before_style.get(field)) is bool:
                    dimension = "removed_true" if before_style[field] else "removed_false"
                counts[f"bullet_{field}_{dimension}"] += 1
    return counts


def project_branded_bullet_styles(document: dict[str, Any], expected: DocumentIR) -> dict[str, Any]:
    actual = parse_google_document(document, title=expected.title)
    aligned = align_branded_bullet_metadata(actual, expected)
    replacements: dict[tuple[str, int], Any] = {
        (block.source_range.tab_id, block.source_range.start_index): json.loads(
            block.native_bullet_json
        )
        for block, original in zip(aligned.blocks, actual.blocks, strict=True)
        if block.source_range is not None
        and block.native_bullet_json is not None
        and block.native_bullet_json != original.native_bullet_json
    }
    if not replacements:
        return document
    projected = deepcopy(document)
    _project_tabs(projected.get("tabs", []), replacements)
    return projected


def _project_tabs(tabs: list[Any], replacements: dict[tuple[str, int], Any]) -> None:
    for tab in tabs:
        tab_id = tab.get("tabProperties", {}).get("tabId")
        for item in tab.get("documentTab", {}).get("body", {}).get("content", []):
            bullet = replacements.get((tab_id, item.get("startIndex")))
            if bullet is not None and isinstance(item.get("paragraph"), dict):
                item["paragraph"]["bullet"] = bullet
        _project_tabs(tab.get("childTabs", []), replacements)


def _only_planned_style_changes(
    original: dict[str, Any], current: dict[str, Any], permitted: dict[str, Any]
) -> bool:
    # Docs UpdateTextStyle can extend to the paragraph newline and apply the
    # same fields to its bullet. Only those exact emitted values are projected;
    # identities, nesting, links, unknown properties and all other styles stay
    # part of the strict baseline. List definitions are never projected.
    before = dict(original)
    after = dict(current)
    before_style = before.pop("textStyle", {})
    after_style = after.pop("textStyle", {})
    # Preserve the observed ID verbatim in the projected result. COPY may prove
    # an independent bijection later; in-place inventory and IR still reject an
    # identity change. A style projection must not itself authorize renaming.
    before.pop("listId", None)
    after.pop("listId", None)
    if before != after or not isinstance(before_style, dict) or not isinstance(after_style, dict):
        return False
    return all(
        (
            field in before_style
            and field in after_style
            and before_style[field] == after_style[field]
        )
        or (
            field in permitted
            and field in after_style
            and _style_value_matches(field, after_style[field], permitted[field])
        )
        for field in before_style.keys() | after_style.keys()
    )


def _style_value_matches(field: str, actual: object, expected: object) -> bool:
    if actual == expected:
        return True
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return False
    if field == "weightedFontFamily":
        # Docs explicitly defaults omitted request weight to 400. Preserve all
        # other fields; in particular never accept a different family or weight.
        return {**actual, "weight": actual.get("weight", 400)} == {
            **expected,
            "weight": expected.get("weight", 400),
        }
    if field != "foregroundColor" or set(actual) != {"color"} or set(expected) != {"color"}:
        return False
    actual_color = actual.get("color")
    expected_color = expected.get("color")
    if (
        not isinstance(actual_color, dict)
        or not isinstance(expected_color, dict)
        or set(actual_color) != {"rgbColor"}
        or set(expected_color) != {"rgbColor"}
    ):
        return False
    actual_rgb = actual_color.get("rgbColor")
    expected_rgb = expected_color.get("rgbColor")
    channels = {"red", "green", "blue"}
    if (
        not isinstance(actual_rgb, dict)
        or not isinstance(expected_rgb, dict)
        or not set(actual_rgb).issubset(channels)
        or not set(expected_rgb).issubset(channels)
    ):
        return False
    return all(
        _rgb_channel_matches(actual_rgb.get(channel, 0), expected_rgb.get(channel, 0))
        for channel in channels
    )


def _rgb_channel_matches(actual: object, expected: object) -> bool:
    return (
        isinstance(actual, int | float)
        and isinstance(expected, int | float)
        and not isinstance(actual, bool)
        and not isinstance(expected, bool)
        and abs(actual - expected) < _RGB_SERIALIZATION_TOLERANCE
    )
