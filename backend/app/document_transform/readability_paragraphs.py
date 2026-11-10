from copy import deepcopy
from dataclasses import replace
import re

from pydantic import JsonValue

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.ir import (
    MAX_DOCUMENT_BLOCKS,
    BlockKind,
    DocumentIR,
    TextBlock,
    parse_google_document,
    utf16_length,
)
from app.document_transform.readability_json import (
    array,
    bodies,
    canonical_style,
    content,
    effective_paragraph_style,
    effective_text_style,
    integer,
    obj,
    plain_text,
    rgb_color,
)
from app.document_transform.readability_types import (
    ReadabilityParagraph,
    ReadabilityPart,
    ReadabilityProposal,
)
from app.identifiers import DocsTabId


_DOCUMENT = CURRENT_BRAND_MANIFEST.document
_STYLE_FIELDS = frozenset(
    {
        "alignment",
        "lineSpacing",
        "spaceAbove",
        "spaceBelow",
        "keepWithNext",
        "keepLinesTogether",
        "namedStyleType",
        "indentStart",
        "indentEnd",
        "indentFirstLine",
        "direction",
        "avoidWidowAndOrphan",
        "spacingMode",
        "pageBreakBefore",
    }
)
_SOURCE_SEQUENCE = re.compile(
    r"\b(?:first|second|third|next|then|finally|steps?|procedure|before|after)\b", re.IGNORECASE
)
_HEADING_WORDS = 12
_MIN_LIST_ITEMS = 2


def readability_document(document: dict[str, JsonValue], *, title: str) -> DocumentIR:
    parsed = parse_google_document(document, title=title)
    tabs = dict(bodies(document))
    positions = {
        (tab_id, integer(obj(value)["startIndex"])): obj(value)
        for tab_id, tab in bodies(document)
        for value in content(tab)
        if "paragraph" in obj(value)
    }
    return replace(
        parsed,
        blocks=tuple(
            replace(block, editable=_eligible_block(block, positions, document=document, tabs=tabs))
            for block in parsed.blocks
        ),
    )


def _eligible_block(
    block: TextBlock,
    positions: dict[tuple[DocsTabId, int], dict[str, JsonValue]],
    *,
    document: dict[str, JsonValue],
    tabs: dict[DocsTabId, dict[str, JsonValue]],
) -> bool:
    source = block.source_range
    return bool(
        block.kind == BlockKind.PARAGRAPH
        and not block.native_content
        and not block.native_bullet_json
        and not block.protected_suffix
        and source is not None
        and source.segment_id is None
        and _eligible_element(
            positions[source.tab_id, source.start_index], document=document, tab=tabs[source.tab_id]
        )
    )


def _eligible_element(
    element: dict[str, JsonValue], *, document: dict[str, JsonValue], tab: dict[str, JsonValue]
) -> bool:
    try:
        return _managed_prose(element, document=document, tab=tab)
    except KeyError, TypeError, ValueError:
        return False


def _managed_prose(
    element: dict[str, JsonValue], *, document: dict[str, JsonValue], tab: dict[str, JsonValue]
) -> bool:
    uniform = _uniform_style(element)
    style = obj(obj(element["paragraph"]).get("paragraphStyle", {}))
    effective = effective_text_style(document, tab, style, uniform)
    if (
        obj(effective.get("weightedFontFamily", {})).get("fontFamily") != _DOCUMENT.font_family
        or effective.get("bold")
        or effective.get("italic")
        or obj(effective.get("fontSize", {})).get("magnitude", _DOCUMENT.body_size_pt)
        != _DOCUMENT.body_size_pt
    ):
        return False
    layout = effective_paragraph_style(document, tab, style)
    if (
        set(style) - _STYLE_FIELDS
        or layout.get("namedStyleType") != "NORMAL_TEXT"
        or layout.get("alignment") != "JUSTIFIED"
        or layout.get("pageBreakBefore")
    ):
        return False
    return not any(
        obj(layout.get(key, {})).get("magnitude", 0)
        for key in ("indentStart", "indentEnd", "indentFirstLine")
    )


def _uniform_style(element: dict[str, JsonValue]) -> dict[str, JsonValue]:
    plain_text(element)
    styles = [
        obj(obj(value)["textRun"]).get("textStyle", {})
        for value in array(obj(element["paragraph"])["elements"])
        if str(obj(obj(value)["textRun"])["content"]).strip()
    ]
    if not styles or any(style != styles[0] for style in styles):
        msg = "Preserve mixed inline formatting"
        raise ValueError(msg)
    style = obj(styles[0])
    if set(style) - {
        "weightedFontFamily",
        "fontSize",
        "foregroundColor",
        "backgroundColor",
        "bold",
        "italic",
        "underline",
        "strikethrough",
        "baselineOffset",
    }:
        msg = "Preserve linked or annotated text"
        raise ValueError(msg)
    if (
        obj(style.get("weightedFontFamily", {})).get("fontFamily")
        not in {None, _DOCUMENT.font_family}
        or style.get("bold")
        or style.get("italic")
    ):
        msg = "Preserve authored typography"
        raise ValueError(msg)
    return style


def validated_readability_paragraphs(
    document: dict[str, JsonValue], ir: DocumentIR, proposals: list[ReadabilityProposal]
) -> tuple[ReadabilityParagraph, ...]:
    by_id = {block.id: block for block in ir.blocks}
    tabs = dict(bodies(document))
    if len({proposal.block_id for proposal in proposals}) != len(proposals):
        msg = "Duplicate readability proposals"
        raise ValueError(msg)
    positions = {
        (tab_id, integer(obj(value)["startIndex"])): obj(value)
        for tab_id, tab in bodies(document)
        for value in content(tab)
        if "paragraph" in obj(value)
    }
    result = []
    for proposal in proposals:
        block = by_id.get(proposal.block_id)
        if (
            block is None
            or not block.editable
            or block.sha256 != proposal.source_sha256
            or block.source_range is None
        ):
            msg = "Readability proposal is not bound to eligible source text"
            raise ValueError(msg)
        _validate_parts(block, proposal)
        if len(proposal.parts) == 1 and proposal.parts[0].role == "paragraph":
            continue
        source = block.source_range
        element = positions[source.tab_id, source.start_index]
        result.append(
            ReadabilityParagraph(
                tab_id=source.tab_id,
                start=source.start_index,
                end=source.end_index,
                source=element,
                text_style=effective_text_style(
                    document,
                    tabs[source.tab_id],
                    obj(obj(element["paragraph"])["paragraphStyle"]),
                    _uniform_style(element),
                ),
                parts=proposal.parts,
            )
        )
    if len(ir.blocks) + sum(len(target.parts) - 1 for target in result) > MAX_DOCUMENT_BLOCKS:
        msg = "Restructuring must stay within the document paragraph limit"
        raise ValueError(msg)
    _validate_groups(result)
    return tuple(result)


def _validate_parts(block: TextBlock, proposal: ReadabilityProposal) -> None:
    if " ".join(part.text for part in proposal.parts) != block.text or any(
        part.text != part.text.strip() or "\n" in part.text or "\t" in part.text
        for part in proposal.parts
    ):
        msg = (
            "Layout must preserve every source character except spaces replaced by paragraph breaks"
        )
        raise ValueError(msg)
    if any(
        part.role.startswith("heading")
        and (len(part.text.split()) > _HEADING_WORDS or part.text.endswith((".", "!", "?")))
        for part in proposal.parts
    ):
        msg = "Use concise existing labels as headings"
        raise ValueError(msg)
    if any(part.role == "numbered" for part in proposal.parts) and not _SOURCE_SEQUENCE.search(
        block.text
    ):
        msg = "Numbered lists require an explicit source sequence"
        raise ValueError(msg)


def _validate_groups(targets: list[ReadabilityParagraph]) -> None:
    # A list must contain parallel items. Do not manufacture a one-item list,
    # or join facts across a tab boundary or an intervening section/table.
    groups: list[list[ReadabilityPart]] = []
    previous_tab = None
    previous_end = -1
    for target in sorted(targets, key=lambda item: (item.tab_id, item.start)):
        adjacent = target.tab_id == previous_tab and target.start == previous_end + 1
        for part in target.parts:
            if part.role in {"bullet", "numbered"}:
                if groups and adjacent and groups[-1][-1].role == part.role:
                    groups[-1].append(part)
                else:
                    groups.append([part])
                adjacent = True
            else:
                adjacent = False
        previous_tab, previous_end = target.tab_id, target.end
    if any(len(group) < _MIN_LIST_ITEMS for group in groups):
        msg = "Lists require at least two adjacent items"
        raise ValueError(msg)


def readable_paragraph_requests(
    targets: tuple[ReadabilityParagraph, ...],
) -> list[dict[str, JsonValue]]:
    requests: list[dict[str, JsonValue]] = []
    # Replace only the separator spaces. Keep words, character styles, and
    # anchors in place, with no net change to Google Docs' UTF-16 indexes.
    for target in sorted(targets, key=lambda item: (item.tab_id, item.start), reverse=True):
        boundaries = []
        index = target.start
        for part in target.parts[:-1]:
            index += utf16_length(part.text)
            boundaries.append(index)
            index += 1
        for index in reversed(boundaries):
            requests.extend(
                [
                    {
                        "deleteContentRange": {
                            "range": {
                                "startIndex": index,
                                "endIndex": index + 1,
                                "tabId": target.tab_id,
                            }
                        }
                    },
                    {
                        "insertText": {
                            "location": {"index": index, "tabId": target.tab_id},
                            "text": "\n",
                        }
                    },
                ]
            )
    for target in targets:
        requests.extend(_paragraph_appearance_requests(target))
    # Create adjacent bullets together so Google starts one ordered list and
    # computes the numbering; never insert literal numbers into the prose.
    for target in targets:
        index = target.start
        for part in target.parts:
            end = index + utf16_length(part.text)
            if part.role in {"bullet", "numbered"}:
                requests.append(
                    {
                        "createParagraphBullets": {
                            "range": {"startIndex": index, "endIndex": end, "tabId": target.tab_id},
                            "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN"
                            if part.role == "numbered"
                            else "BULLET_DISC_CIRCLE_SQUARE",
                        }
                    }
                )
            index = end + 1
    return requests


def _paragraph_appearance_requests(target: ReadabilityParagraph) -> list[dict[str, JsonValue]]:
    requests: list[dict[str, JsonValue]] = []
    index = target.start
    for part in target.parts:
        end = index + utf16_length(part.text)
        location: dict[str, JsonValue] = {
            "startIndex": index,
            "endIndex": end,
            "tabId": target.tab_id,
        }
        style = _part_style(part)
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": location,
                    "paragraphStyle": style,
                    "fields": ",".join(style),
                }
            }
        )
        text_style = _part_text_style(target, part)
        requests.append(
            {
                "updateTextStyle": {
                    "range": location,
                    "textStyle": text_style,
                    "fields": ",".join(text_style),
                }
            }
        )
        index = end + 1
    return requests


def _part_style(part: ReadabilityPart) -> dict[str, JsonValue]:
    heading = part.role.startswith("heading")
    listed = part.role in {"bullet", "numbered"}
    return {
        "namedStyleType": "HEADING_2"
        if part.role == "heading2"
        else "HEADING_3"
        if heading
        else "NORMAL_TEXT",
        "alignment": "JUSTIFIED" if part.role == "paragraph" else "START",
        "lineSpacing": 100 if listed else _DOCUMENT.line_spacing_percent,
        "spaceAbove": {
            "magnitude": 12 if part.role == "heading2" else 6 if heading else 0,
            "unit": "PT",
        },
        "spaceBelow": {"magnitude": 2 if listed else 6, "unit": "PT"},
        "keepWithNext": heading,
        "keepLinesTogether": heading,
    }


def project_readable_paragraphs(
    document: dict[str, JsonValue], targets: tuple[ReadabilityParagraph, ...], *, applied: bool
) -> None:
    tabs = dict(bodies(document))
    for target in sorted(targets, key=lambda item: (item.tab_id, item.start), reverse=True):
        values = content(tabs[target.tab_id])
        matches = [
            index
            for index, value in enumerate(values)
            if obj(value).get("startIndex") == target.start
        ]
        if len(matches) != 1:
            msg = "Paragraph identity changed"
            raise ValueError(msg)
        ordinal = matches[0]
        if applied:
            index = target.start
            for offset, part in enumerate(target.parts):
                element = obj(values[ordinal + offset])
                if part.role in {"bullet", "numbered"}:
                    _verify_bullet(
                        obj(element["paragraph"]), obj(tabs[target.tab_id].get("lists", {})), part
                    )
                _verify_part(element, part, start=index)
                _verify_part_layout(document, tabs[target.tab_id], element, part, target=target)
                _verify_part_text_style(document, tabs[target.tab_id], element, part, target=target)
                index += utf16_length(part.text) + 1
            values[ordinal : ordinal + len(target.parts)] = [deepcopy(target.source)]
        elif values[ordinal] != target.source:
            msg = "Paragraph source changed"
            raise ValueError(msg)


def _verify_part(
    element: dict[str, JsonValue],
    part: ReadabilityPart,
    *,
    start: int,
) -> None:
    paragraph = obj(element["paragraph"])
    listed = part.role in {"bullet", "numbered"}
    bullet = paragraph.pop("bullet", None)
    if listed != isinstance(bullet, dict):
        msg = "List formatting was not applied"
        raise ValueError(msg)
    if (
        plain_text(element) != part.text + "\n"
        or element.get("startIndex") != start
        or element.get("endIndex") != start + utf16_length(part.text) + 1
    ):
        msg = "Paragraph text or position changed"
        raise ValueError(msg)


def _verify_part_layout(
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    element: dict[str, JsonValue],
    part: ReadabilityPart,
    *,
    target: ReadabilityParagraph,
) -> None:
    observed = dict(obj(obj(element["paragraph"])["paragraphStyle"]))
    if part.role.startswith("heading"):
        observed.pop("headingId", None)
    wanted: dict[str, JsonValue] = {
        **obj(obj(target.source["paragraph"]).get("paragraphStyle", {})),
        **_part_style(part),
    }
    if part.role in {"bullet", "numbered"}:
        wanted.update(
            {
                "indentStart": {"magnitude": 36, "unit": "PT"},
                "indentFirstLine": {"magnitude": 18, "unit": "PT"},
            }
        )
    actual = effective_paragraph_style(document, tab, observed)
    expected = effective_paragraph_style(document, tab, wanted)
    if canonical_style(actual) != canonical_style(expected):
        msg = "Paragraph formatting was not applied or unplanned properties changed"
        raise ValueError(msg)


def _verify_part_text_style(
    document: dict[str, JsonValue],
    tab: dict[str, JsonValue],
    element: dict[str, JsonValue],
    part: ReadabilityPart,
    *,
    target: ReadabilityParagraph,
) -> None:
    paragraph = obj(element["paragraph"])
    wanted = _part_text_style(target, part)
    for value in array(paragraph["elements"]):
        run = obj(obj(value)["textRun"])
        observed = effective_text_style(
            document, tab, obj(paragraph["paragraphStyle"]), obj(run.get("textStyle", {}))
        )
        if run["content"] != "\n" and canonical_style(observed) != canonical_style(wanted):
            msg = "Unplanned inline formatting changed"
            raise ValueError(msg)


def _verify_bullet(
    paragraph: dict[str, JsonValue], lists: dict[str, JsonValue], part: ReadabilityPart
) -> None:
    bullet = obj(paragraph.get("bullet", {}))
    if set(bullet) - {"listId", "nestingLevel", "textStyle"} or bullet.get("nestingLevel", 0) != 0:
        raise ValueError
    definition = obj(lists[str(bullet["listId"])])
    first = obj(array(obj(definition["listProperties"])["nestingLevels"])[0])
    if part.role == "numbered":
        if first.get("glyphType") != "DECIMAL":
            raise ValueError
    elif not first.get("glyphSymbol") or first.get("glyphType") not in {
        None,
        "GLYPH_TYPE_UNSPECIFIED",
    }:
        raise ValueError


def _part_text_style(target: ReadabilityParagraph, part: ReadabilityPart) -> dict[str, JsonValue]:
    style = deepcopy(target.text_style)
    if part.role.startswith("heading"):
        style.update(
            {
                "fontSize": {
                    "magnitude": _DOCUMENT.h2_size_pt
                    if part.role == "heading2"
                    else _DOCUMENT.h3_size_pt,
                    "unit": "PT",
                },
                "bold": True,
                "foregroundColor": {
                    "color": {"rgbColor": rgb_color(CURRENT_BRAND_MANIFEST.colors.primary)}
                },
            }
        )
    return style
