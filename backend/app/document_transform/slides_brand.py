from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
from operator import itemgetter
from typing import Any, NamedTuple

from pydantic import JsonValue

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.ir import DocumentIR, TextBlock, utf16_length
from app.document_transform.presentation_gateway import PresentationFingerprints
from app.document_transform.schemas import TransformFinding, TransformMode
from app.document_transform.slides_ir import (
    ParsedPresentation,
    PlainSlideText,
    SlideId,
    SlideObjectId,
    SlideTextLocator,
    parse_google_presentation,
    parse_plain_slide_text,
    sarj_logo_object_id,
)


MAX_SLIDES_MUTATION_REQUESTS = 1_000
MAX_SLIDES_MUTATION_BYTES = 1_000_000
_EMU_PER_POINT = 12_700
_PRESERVED_TEXT_STYLE_FIELDS = (
    "backgroundColor",
    "baselineOffset",
    "bold",
    "italic",
    "link",
    "smallCaps",
    "strikethrough",
    "underline",
)
_SUPPORTED_PLACEHOLDER_TYPES = frozenset(
    {
        "BODY",
        "CENTERED_TITLE",
        "DATE_AND_TIME",
        "FOOTER",
        "HEADER",
        "OBJECT",
        "SLIDE_NUMBER",
        "SUBTITLE",
        "TITLE",
    }
)
_MIN_TEXT_CONTRAST = 4.5
_SRGB_LINEAR_THRESHOLD = 0.04045
_PLACEHOLDER_STYLE_LEVEL_COUNTS = frozenset({8, 9})
_CANVAS_ASPECT_REL_TOLERANCE = 1e-6
# Advisory only: long narrative text below this floor warrants human review, never resizing.
_MIN_NARRATIVE_TEXT_SIZE_PT = 12
_MIN_NARRATIVE_TEXT_CHARS = 80
_NARRATIVE_PLACEHOLDER_TYPES = frozenset({"BODY", "OBJECT"})


@dataclass(frozen=True, slots=True)
class _Rect:
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class _Affine:
    scale_x: float
    scale_y: float
    shear_x: float
    shear_y: float
    translate_x: float
    translate_y: float


@dataclass(frozen=True, slots=True)
class _StyleRun:
    ordinal: int
    start_index: int
    end_index: int
    preserved_style_json: str
    brand_style_json: str
    font_weight: int


@dataclass(frozen=True, slots=True)
class _StyleTarget:
    object_id: SlideObjectId
    placeholder_type: str | None
    runs: tuple[_StyleRun, ...]


@dataclass(frozen=True, slots=True)
class _LogoLayout:
    page_width: float
    page_height: float
    width: float
    height: float
    candidates: tuple[_Rect, ...]


class _StylePlan(NamedTuple):
    requests: list[dict[str, Any]]
    targets: tuple[_StyleTarget, ...]
    findings: list[TransformFinding]


class _DesiredTextStyle(NamedTuple):
    style: dict[str, Any]
    fields: str


class _StyleScan(NamedTuple):
    targets: tuple[_StyleTarget, ...]
    findings: list[TransformFinding]


class _StyleDecision(NamedTuple):
    target: _StyleTarget | None
    finding: TransformFinding | None


class _StyleEligibility(NamedTuple):
    placeholder_type: str
    desired_style: dict[str, Any]
    background: str


class _StyleEligibilityDecision(NamedTuple):
    eligibility: _StyleEligibility | None
    finding: TransformFinding | None


class _StyleRunDecision(NamedTuple):
    run: _StyleRun | None
    finding: TransformFinding | None


class _LogoPlan(NamedTuple):
    requests: list[dict[str, Any]]
    placements: dict[SlideId, _Rect]
    findings: list[TransformFinding]


class _LogoDecision(NamedTuple):
    request: dict[str, Any] | None
    placement: _Rect | None
    finding: TransformFinding | None


class _LogoRemovalDecision(NamedTuple):
    request: dict[str, Any] | None
    finding: TransformFinding | None


class _PageSize(NamedTuple):
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class SlideMutationPlan:
    requests: list[dict[str, Any]]
    findings: tuple[TransformFinding, ...]
    mutation_sha256: str
    expected_brand_sha256: str | None


def build_slides_mutation_plan(
    presentation: dict[str, Any],
    *,
    parsed: ParsedPresentation,
    transformed: DocumentIR,
    mode: TransformMode,
    logo_uri: str,
) -> SlideMutationPlan:
    text_requests = _presentation_text_requests(parsed=parsed, transformed=transformed)

    requests: list[dict[str, Any]] = []
    findings: list[TransformFinding] = []
    style_targets: tuple[_StyleTarget, ...] = ()
    logo_rects: dict[SlideId, _Rect] = {}
    if mode is TransformMode.UNSLOP:
        requests.extend(text_requests)
        _, scan_findings = _style_targets(presentation)
        findings.extend(
            finding for finding in scan_findings if finding.code == "slide_text_target_unsupported"
        )
    else:
        style_requests, style_targets, style_findings = _text_style_requests(presentation)
        requests.extend(style_requests)
        findings.extend(style_findings)
        logo_requests, logo_rects, logo_findings = _logo_requests(
            presentation,
            logo_uri=logo_uri,
        )
        requests.extend(logo_requests)
        findings.extend(logo_findings)
        # Style the original visible range before safe deletion/case deltas shorten it.
        requests.extend(text_requests)
    digest = slides_mutation_sha256(requests)
    expected_brand = (
        None
        if mode is TransformMode.UNSLOP
        else _expected_brand_sha256(style_targets=style_targets, logo_rects=logo_rects)
    )
    return SlideMutationPlan(
        requests=requests,
        findings=tuple(findings),
        mutation_sha256=digest,
        expected_brand_sha256=expected_brand,
    )


def _presentation_text_requests(
    *,
    parsed: ParsedPresentation,
    transformed: DocumentIR,
) -> list[dict[str, Any]]:
    transformed_by_id = {block.id: block for block in transformed.blocks}
    requests: list[dict[str, Any]] = []
    for block in reversed(parsed.document.blocks):
        replacement = transformed_by_id[block.id].text
        if replacement != block.text:
            requests.extend(
                _text_delta_requests(
                    block,
                    replacement=replacement,
                    locator=parsed.locators[block.id],
                )
            )
    return requests


def _text_delta_requests(
    block: TextBlock,
    *,
    replacement: str,
    locator: SlideTextLocator,
) -> list[dict[str, Any]]:
    deltas: list[tuple[int, list[dict[str, Any]]]] = []
    offsets = [0]
    for character in block.text:
        offsets.append(offsets[-1] + utf16_length(character))
    source_index = len(block.text) - 1
    delete_end: int | None = None
    for target_character in reversed(replacement):
        while (
            source_index >= 0 and block.text[source_index].casefold() != target_character.casefold()
        ):
            delete_end = source_index + 1 if delete_end is None else delete_end
            source_index -= 1
        if source_index < 0:
            raise UnsupportedSourceError
        if delete_end is not None:
            _append_delete_delta(
                deltas,
                object_id=locator.object_id,
                locator_start=locator.start_index,
                offsets=offsets,
                source_start=source_index + 1,
                source_end=delete_end,
            )
            delete_end = None
        source_character = block.text[source_index]
        if source_character != target_character:
            _append_case_delta(
                deltas,
                object_id=locator.object_id,
                absolute_start=locator.start_index + offsets[source_index],
                absolute_end=locator.start_index + offsets[source_index + 1],
                target_character=target_character,
            )
        source_index -= 1
    if source_index >= 0:
        _append_delete_delta(
            deltas,
            object_id=locator.object_id,
            locator_start=locator.start_index,
            offsets=offsets,
            source_start=0,
            source_end=source_index + 1,
        )
    return [
        request
        for _, requests in sorted(deltas, key=itemgetter(0), reverse=True)
        for request in requests
    ]


def _append_delete_delta(
    deltas: list[tuple[int, list[dict[str, Any]]]],
    *,
    object_id: SlideObjectId,
    locator_start: int,
    offsets: list[int],
    source_start: int,
    source_end: int,
) -> None:
    absolute_start = locator_start + offsets[source_start]
    absolute_end = locator_start + offsets[source_end]
    deltas.append(
        (
            absolute_start,
            [_delete_text_request(object_id, absolute_start, absolute_end)],
        )
    )


def _append_case_delta(
    deltas: list[tuple[int, list[dict[str, Any]]]],
    *,
    object_id: SlideObjectId,
    absolute_start: int,
    absolute_end: int,
    target_character: str,
) -> None:
    deltas.append(
        (
            absolute_start,
            [
                _delete_text_request(object_id, absolute_start, absolute_end),
                {
                    "insertText": {
                        "objectId": object_id,
                        "insertionIndex": absolute_start,
                        "text": target_character,
                    }
                },
            ],
        )
    )


def _delete_text_request(object_id: SlideObjectId, start: int, end: int) -> dict[str, JsonValue]:
    if end <= start:
        raise UnsupportedSourceError
    return {
        "deleteText": {
            "objectId": object_id,
            "textRange": {
                "startIndex": start,
                "endIndex": end,
                "type": "FIXED_RANGE",
            },
        }
    }


def slides_mutation_sha256(requests: list[dict[str, Any]]) -> str:
    if len(requests) > MAX_SLIDES_MUTATION_REQUESTS:
        raise SourceTooLargeError
    payload = json.dumps(
        requests, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode()
    if len(payload) > MAX_SLIDES_MUTATION_BYTES:
        raise SourceTooLargeError
    return hashlib.sha256(payload).hexdigest()


def presentation_fingerprint(
    presentation: dict[str, Any],
    title: str,
) -> PresentationFingerprints:
    parsed = parse_google_presentation(presentation, title=title)
    style_targets, _ = _style_targets(presentation)
    brand_sha256 = _brand_digest(
        styles=[_observed_style(target) for target in style_targets],
        logos=_observed_logos(presentation),
    )
    return PresentationFingerprints(
        semantic_sha256=parsed.document.semantic_sha256,
        protected_structure_sha256=parsed.protected_sha256,
        brand_sha256=brand_sha256,
    )


def _text_style_requests(
    presentation: dict[str, Any],
) -> _StylePlan:
    targets, findings = _style_targets(presentation)
    requests = []
    for target in targets:
        style, fields = _desired_style(
            placeholder_type=target.placeholder_type,
            font_weight=target.runs[0].font_weight,
        )
        requests.append(
            {
                "updateTextStyle": {
                    "objectId": target.object_id,
                    "style": style,
                    "textRange": {
                        "type": "FIXED_RANGE",
                        "startIndex": target.runs[0].start_index,
                        "endIndex": target.runs[0].end_index,
                    },
                    "fields": fields,
                }
            }
        )
    return _StylePlan(requests, targets, findings)


def _desired_style(
    *,
    placeholder_type: str | None,
    font_weight: int = 400,
) -> _DesiredTextStyle:
    manifest = CURRENT_BRAND_MANIFEST
    slides = manifest.slides
    colors = manifest.colors
    title = placeholder_type in {"TITLE", "CENTERED_TITLE"}
    subtitle = placeholder_type == "SUBTITLE"
    caption = placeholder_type in {"DATE_AND_TIME", "FOOTER", "HEADER", "SLIDE_NUMBER"}
    size = (
        slides.deck_title_size_pt if placeholder_type == "CENTERED_TITLE" else slides.title_size_pt
    )
    if not title:
        size = slides.caption_size_pt if caption else slides.body_size_pt
    color = colors.primary_dark if title else colors.muted if subtitle or caption else colors.ink
    style: dict[str, Any] = {
        "weightedFontFamily": {
            "fontFamily": manifest.document.font_family,
            "weight": font_weight,
        },
        "fontSize": {"magnitude": size, "unit": "PT"},
        "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(color)}},
    }
    return _DesiredTextStyle(style, "weightedFontFamily,fontSize,foregroundColor")


def _style_targets(
    presentation: dict[str, Any],
) -> _StyleScan:
    targets: list[_StyleTarget] = []
    findings: list[TransformFinding] = []
    object_ids = _object_id_occurrences(presentation)
    for slide in _slides(presentation):
        theme_colors = _effective_theme_colors(presentation, slide)
        page_background = _effective_page_background(presentation, slide)
        if _has_visual_underlay(presentation, slide):
            page_background = None
        for element in _walk_elements(slide.get("pageElements", [])):
            target, finding = _style_target(
                element,
                page_background=page_background,
                theme_colors=theme_colors,
                object_ids=object_ids,
            )
            if target is not None:
                targets.append(target)
            if finding is not None:
                findings.append(finding)
            small_text_finding = _small_narrative_text_finding(
                element,
                page_background=page_background,
                theme_colors=theme_colors,
                object_ids=object_ids,
            )
            if small_text_finding is not None:
                findings.append(small_text_finding)
    return _StyleScan(tuple(targets), findings)


def _style_target(
    element: dict[str, Any],
    *,
    page_background: str | None,
    theme_colors: dict[str, str] | None,
    object_ids: dict[str, tuple[dict[str, Any], ...]],
) -> _StyleDecision:
    object_id = SlideObjectId(_required_string(element, "objectId"))
    shape = element.get("shape")
    if not isinstance(shape, dict):
        finding = _unsupported_text_finding(object_id) if _has_visible_text(element) else None
        return _StyleDecision(None, finding)
    text = shape.get("text")
    if text is None:
        return _StyleDecision(None, None)
    if not isinstance(text, dict) or not isinstance(text.get("textElements"), list):
        return _StyleDecision(None, _unsupported_text_finding(object_id))
    plain_text = parse_plain_slide_text(text["textElements"])
    if plain_text is None:
        return _StyleDecision(None, _unsupported_text_finding(object_id))
    ancestors = _placeholder_ancestors(shape, object_ids=object_ids)
    if ancestors is None:
        return _StyleDecision(None, _inherited_style_finding(object_id))
    inherited_styles = _inherited_text_styles(ancestors)
    if inherited_styles is None:
        return _StyleDecision(None, _inherited_style_finding(object_id))
    eligibility, finding = _style_eligibility(
        element,
        shape=shape,
        object_id=object_id,
        page_background=page_background,
        theme_colors=theme_colors,
        ancestors=ancestors,
    )
    if eligibility is None:
        return _StyleDecision(None, finding)
    run, finding = _target_style_run(
        plain_text,
        object_id=object_id,
        desired_style=eligibility.desired_style,
        background=eligibility.background,
        theme_colors=theme_colors,
        inherited_styles=inherited_styles,
    )
    if run is None:
        return _StyleDecision(None, finding)
    return _StyleDecision(
        _StyleTarget(
            object_id=object_id,
            placeholder_type=eligibility.placeholder_type,
            runs=(run,),
        ),
        None,
    )


def _style_eligibility(
    element: dict[str, Any],
    *,
    shape: dict[str, Any],
    object_id: SlideObjectId,
    page_background: str | None,
    theme_colors: dict[str, str] | None,
    ancestors: tuple[dict[str, Any], ...],
) -> _StyleEligibilityDecision:
    placeholder_type = _placeholder_type(shape)
    if placeholder_type not in _SUPPORTED_PLACEHOLDER_TYPES:
        return _StyleEligibilityDecision(None, _role_unresolved_finding(object_id))
    autofit = _inherited_shape_property(element, ancestors=ancestors, key="autofit")
    if not isinstance(autofit, dict) or autofit.get("autofitType") != "NONE":
        return _StyleEligibilityDecision(None, _fit_unresolved_finding(object_id))
    desired_style, _ = _desired_style(
        placeholder_type=placeholder_type,
    )
    background = _effective_shape_background(
        element,
        ancestors=ancestors,
        page_background=page_background,
        theme_colors=theme_colors,
    )
    desired_color = _canonical_style(desired_style)["color"]
    if (
        background is None
        or not isinstance(desired_color, str)
        or _contrast_ratio(desired_color, background) < _MIN_TEXT_CONTRAST
    ):
        return _StyleEligibilityDecision(None, _contrast_unresolved_finding(object_id))
    return _StyleEligibilityDecision(
        _StyleEligibility(placeholder_type, desired_style, background),
        None,
    )


def _small_narrative_text_finding(
    element: dict[str, Any],
    *,
    page_background: str | None,
    theme_colors: dict[str, str] | None,
    object_ids: dict[str, tuple[dict[str, Any], ...]],
) -> TransformFinding | None:
    object_id = SlideObjectId(_required_string(element, "objectId"))
    shape = element.get("shape")
    if not isinstance(shape, dict) or _placeholder_type(shape) not in _NARRATIVE_PLACEHOLDER_TYPES:
        return None
    text = shape.get("text")
    elements = text.get("textElements") if isinstance(text, dict) else None
    if not isinstance(elements, list):
        return None
    plain_text = parse_plain_slide_text(elements)
    if plain_text is None or len(" ".join(plain_text.text.split())) < _MIN_NARRATIVE_TEXT_CHARS:
        return None
    ancestors = _placeholder_ancestors(shape, object_ids=object_ids)
    if ancestors is None:
        return None
    inherited_styles = _inherited_text_styles(ancestors)
    if inherited_styles is None:
        return None
    autofit = _inherited_shape_property(element, ancestors=ancestors, key="autofit")
    if not isinstance(autofit, dict) or autofit.get("autofitType") != "NONE":
        return None
    if not isinstance(plain_text.style, dict):
        return None
    style = _effective_text_style(
        {str(key): value for key, value in plain_text.style.items()},
        inherited_styles=inherited_styles,
    )
    if style.get("link") is not None:
        return None
    observed = _canonical_style(style, theme_colors=theme_colors)
    font_family = observed["fontFamily"]
    font_size = observed["fontSizePt"]
    color = observed["color"]
    if (
        not isinstance(font_family, str)
        or isinstance(font_size, bool)
        or not isinstance(font_size, int | float)
        or font_size >= _MIN_NARRATIVE_TEXT_SIZE_PT
        or not isinstance(color, str)
    ):
        return None
    background = _effective_shape_background(
        element,
        ancestors=ancestors,
        page_background=page_background,
        theme_colors=theme_colors,
    )
    if background is None:
        return None
    text_background = _text_background(
        style,
        fallback=background,
        theme_colors=theme_colors,
    )
    if text_background is None or _contrast_ratio(color, text_background) < _MIN_TEXT_CONTRAST:
        return None
    return _small_text_finding(object_id)


def _target_style_run(
    plain_text: PlainSlideText,
    *,
    object_id: SlideObjectId,
    desired_style: dict[str, Any],
    background: str,
    theme_colors: dict[str, str] | None,
    inherited_styles: tuple[dict[str, Any], ...],
) -> _StyleRunDecision:
    if not isinstance(plain_text.style, dict):
        return _StyleRunDecision(None, _unresolved_style_finding(object_id))
    explicit_style = {str(key): value for key, value in plain_text.style.items()}
    style = _effective_text_style(explicit_style, inherited_styles=inherited_styles)
    if style.get("link") is not None:
        return _StyleRunDecision(None, _unsupported_text_finding(object_id))
    if not plain_text.text.strip():
        return _StyleRunDecision(None, None)
    observed_brand = _canonical_style(style, theme_colors=theme_colors)
    if any(observed_brand[key] is None for key in ("fontFamily", "fontSizePt", "color")):
        return _StyleRunDecision(None, _inherited_style_finding(object_id))
    desired_brand = _canonical_style(desired_style)
    desired_family = desired_brand["fontFamily"]
    observed_family = observed_brand["fontFamily"]
    if (
        not isinstance(desired_family, str)
        or not isinstance(observed_family, str)
        or desired_family.casefold() != observed_family.casefold()
    ):
        return _StyleRunDecision(None, _fit_unresolved_finding(object_id))
    desired_size = desired_brand["fontSizePt"]
    observed_size = observed_brand["fontSizePt"]
    if (
        not isinstance(desired_size, int | float)
        or not isinstance(observed_size, int | float)
        or desired_size > observed_size
    ):
        return _StyleRunDecision(None, _fit_unresolved_finding(object_id))
    text_background = _text_background(
        style,
        fallback=background,
        theme_colors=theme_colors,
    )
    desired_color = _canonical_style(desired_style)["color"]
    if (
        text_background is None
        or not isinstance(desired_color, str)
        or _contrast_ratio(desired_color, text_background) < _MIN_TEXT_CONTRAST
    ):
        return _StyleRunDecision(None, _contrast_unresolved_finding(object_id))
    weight = observed_brand["fontWeight"]
    if not isinstance(weight, int):
        return _StyleRunDecision(None, _inherited_style_finding(object_id))
    return _StyleRunDecision(
        _StyleRun(
            ordinal=0,
            start_index=plain_text.start_index,
            end_index=plain_text.end_index,
            preserved_style_json=_canonical_json(
                {
                    key: explicit_style[key]
                    for key in _PRESERVED_TEXT_STYLE_FIELDS
                    if key in explicit_style
                }
            ),
            brand_style_json=_canonical_json(observed_brand),
            font_weight=weight,
        ),
        None,
    )


def _placeholder_type(shape: dict[str, Any]) -> str | None:
    placeholder = shape.get("placeholder")
    value = placeholder.get("type") if isinstance(placeholder, dict) else None
    if value is not None and not isinstance(value, str):
        raise UnsupportedSourceError
    return value


def _placeholder_ancestors(
    shape: dict[str, Any],
    *,
    object_ids: dict[str, tuple[dict[str, Any], ...]],
) -> tuple[dict[str, Any], ...] | None:
    placeholder = shape.get("placeholder")
    if placeholder is None:
        return ()
    if not isinstance(placeholder, dict):
        return None
    parent_id = placeholder.get("parentObjectId")
    ancestors: list[dict[str, Any]] = []
    seen: set[str] = set()
    while parent_id is not None:
        if not isinstance(parent_id, str) or parent_id in seen:
            return None
        seen.add(parent_id)
        occurrences = object_ids.get(parent_id, ())
        if len(occurrences) != 1:
            return None
        parent = occurrences[0]
        parent_shape = parent.get("shape")
        if not isinstance(parent_shape, dict):
            return None
        ancestors.append(parent)
        parent_placeholder = parent_shape.get("placeholder")
        if parent_placeholder is None:
            parent_id = None
        elif isinstance(parent_placeholder, dict):
            parent_id = parent_placeholder.get("parentObjectId")
        else:
            return None
    return tuple(ancestors)


def _inherited_text_styles(
    ancestors: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...] | None:
    styles: list[dict[str, Any]] = []
    for ancestor in ancestors:
        shape = ancestor.get("shape")
        text = shape.get("text") if isinstance(shape, dict) else None
        elements = text.get("textElements") if isinstance(text, dict) else None
        style = _inherited_level_zero_style(elements)
        if style is None:
            return None
        styles.append(style)
    return tuple(styles)


def _inherited_level_zero_style(value: object) -> dict[str, Any] | None:
    single_paragraph_style = _single_paragraph_inherited_style(value)
    if single_paragraph_style is not None:
        return single_paragraph_style
    if (
        not isinstance(value, list)
        or len(value) % 2 != 0
        or len(value) // 2 not in _PLACEHOLDER_STYLE_LEVEL_COUNTS
    ):
        return None
    level_count = len(value) // 2
    level_zero_style: dict[str, Any] | None = None
    for level in range(level_count):
        paragraph_element = value[level * 2]
        run_element = value[level * 2 + 1]
        if not isinstance(paragraph_element, dict) or not isinstance(run_element, dict):
            return None
        paragraph_element = {str(key): child for key, child in paragraph_element.items()}
        run_element = {str(key): child for key, child in run_element.items()}
        paragraph = paragraph_element.get("paragraphMarker")
        bullet = paragraph.get("bullet") if isinstance(paragraph, dict) else None
        run = run_element.get("textRun")
        if (
            not _has_exact_text_element_kind(paragraph_element, "paragraphMarker")
            or not _has_exact_text_element_kind(run_element, "textRun")
            or not isinstance(bullet, dict)
            or not isinstance(run, dict)
        ):
            return None
        nesting_level = bullet.get("nestingLevel", 0)
        if (
            isinstance(nesting_level, bool)
            or not isinstance(nesting_level, int)
            or nesting_level != level
            or run.get("content") != "\n"
        ):
            return None
        style = run.get("style")
        if (
            not _style_table_run_bounds_are_valid(
                paragraph_element,
                expected_start=level,
            )
            or not _style_table_run_bounds_are_valid(
                run_element,
                expected_start=level,
            )
            or not isinstance(style, dict)
        ):
            return None
        if level == 0:
            level_zero_style = {str(key): child for key, child in style.items()}
    return level_zero_style


def _single_paragraph_inherited_style(value: object) -> dict[str, Any] | None:
    if not isinstance(value, list) or len(value) not in {2, 3}:
        return None
    paragraph_element, visible_element, *terminal_elements = value
    if not isinstance(paragraph_element, dict) or not isinstance(visible_element, dict):
        return None
    paragraph_element = {str(key): child for key, child in paragraph_element.items()}
    visible_element = {str(key): child for key, child in visible_element.items()}
    paragraph = paragraph_element.get("paragraphMarker")
    visible_run = visible_element.get("textRun")
    if (
        not _has_exact_text_element_kind(paragraph_element, "paragraphMarker")
        or not _has_exact_text_element_kind(visible_element, "textRun")
        or not isinstance(paragraph, dict)
        or paragraph.get("bullet") is not None
        or not isinstance(visible_run, dict)
    ):
        return None
    visible_content = visible_run.get("content")
    if not isinstance(visible_content, str):
        return None
    visible_start = visible_element.get("startIndex", 0)
    visible_end = visible_element.get("endIndex")
    if not isinstance(visible_start, int) or not isinstance(visible_end, int):
        return None
    if terminal_elements:
        terminal_element = terminal_elements[0]
        if not isinstance(terminal_element, dict):
            return None
        terminal_element = {str(key): child for key, child in terminal_element.items()}
        terminal_run = terminal_element.get("textRun")
        if "\n" in visible_content or "\r" in visible_content:
            return None
        if (
            not _has_exact_text_element_kind(terminal_element, "textRun")
            or not isinstance(terminal_run, dict)
            or terminal_run.get("content") != "\n"
            or not _text_element_bounds_are_valid(
                terminal_element,
                expected_start=visible_end,
                expected_end=visible_end + 1,
            )
        ):
            return None
        terminal_style = terminal_run.get("style")
        paragraph_end = visible_end + 1
        if not isinstance(terminal_style, dict):
            return None
    else:
        if (
            not visible_content.endswith("\n")
            or "\n" in visible_content[:-1]
            or "\r" in visible_content
        ):
            return None
        terminal_style = visible_run.get("style")
        paragraph_end = visible_end
        if not isinstance(terminal_style, dict):
            return None
    if (
        not _text_element_bounds_are_valid(
            visible_element,
            expected_start=visible_start,
            expected_end=visible_end,
        )
        or visible_end - visible_start != utf16_length(visible_content)
        or not _text_element_bounds_are_valid(
            paragraph_element,
            expected_start=visible_start,
            expected_end=paragraph_end,
        )
    ):
        return None
    return {str(key): child for key, child in terminal_style.items()}


def _has_exact_text_element_kind(value: dict[str, object], expected: str) -> bool:
    text_kinds = {"autoText", "paragraphMarker", "textRun"}
    return set(value).intersection(text_kinds) == {expected}


def _style_table_run_bounds_are_valid(
    value: dict[str, object],
    *,
    expected_start: int,
) -> bool:
    return _text_element_bounds_are_valid(
        value,
        expected_start=expected_start,
        expected_end=expected_start + 1,
    )


def _text_element_bounds_are_valid(
    value: dict[str, object],
    *,
    expected_start: int,
    expected_end: int,
) -> bool:
    start = value.get("startIndex", 0)
    end = value.get("endIndex")
    return (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and start == expected_start
        and end == expected_end
    )


def _inherited_shape_property(
    element: dict[str, Any],
    *,
    ancestors: tuple[dict[str, Any], ...],
    key: str,
) -> object:
    for candidate in (element, *ancestors):
        shape = candidate.get("shape")
        properties = shape.get("shapeProperties") if isinstance(shape, dict) else None
        if isinstance(properties, dict) and key in properties:
            return properties[key]
    return None


def _effective_text_style(
    explicit: dict[str, Any],
    *,
    inherited_styles: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    effective: dict[str, Any] = {}
    for style in (*reversed(inherited_styles), explicit):
        for key, value in style.items():
            existing = effective.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                effective[key] = {**existing, **value}
            else:
                effective[key] = value
    return effective


def _text_background(
    style: dict[str, Any],
    *,
    fallback: str,
    theme_colors: dict[str, str] | None,
) -> str | None:
    background = style.get("backgroundColor")
    if background is None:
        return fallback
    if not isinstance(background, dict):
        return None
    opaque = background.get("opaqueColor")
    if not isinstance(opaque, dict):
        return None
    return _hex_from_opaque_color(opaque, theme_colors=theme_colors)


def _has_visible_text(value: object) -> bool:
    if isinstance(value, list):
        return any(_has_visible_text(child) for child in value)
    if not isinstance(value, dict):
        return False
    return any(
        (key in {"content", "renderedText"} and isinstance(child, str) and bool(child.strip()))
        or _has_visible_text(child)
        for key, child in value.items()
    )


def _unsupported_text_finding(object_id: SlideObjectId) -> TransformFinding:
    return TransformFinding(
        code="slide_text_target_unsupported",
        block_id=object_id,
        detail="Visible text was preserved but not restyled because its native target is unsupported",
    )


def _unresolved_style_finding(object_id: SlideObjectId) -> TransformFinding:
    return TransformFinding(
        code="slide_text_style_unresolved",
        block_id=object_id,
        detail="Visible text was preserved but not restyled because its style was not explicit",
    )


def _role_unresolved_finding(object_id: SlideObjectId) -> TransformFinding:
    return TransformFinding(
        code="slide_text_role_unresolved",
        block_id=object_id,
        detail="Visible custom text was preserved because its semantic role could not be proven",
    )


def _inherited_style_finding(object_id: SlideObjectId) -> TransformFinding:
    return TransformFinding(
        code="slide_text_style_inherited_unresolved",
        block_id=object_id,
        detail="Visible text was preserved because its effective inherited style was not explicit",
    )


def _fit_unresolved_finding(object_id: SlideObjectId) -> TransformFinding:
    return TransformFinding(
        code="slide_text_fit_unresolved",
        block_id=object_id,
        detail="Visible text was preserved because a typography change could not prove safe fit",
    )


def _contrast_unresolved_finding(object_id: SlideObjectId) -> TransformFinding:
    return TransformFinding(
        code="slide_text_contrast_unresolved",
        block_id=object_id,
        detail="Visible text was preserved because brand-color contrast could not be proven",
    )


def _effective_page_background(
    presentation: dict[str, Any],
    slide: dict[str, Any],
) -> str | None:
    try:
        pages = _page_chain(presentation, slide)
    except UnsupportedSourceError:
        return None
    for page in pages:
        page_properties = page.get("pageProperties")
        if page_properties is None:
            continue
        if not isinstance(page_properties, dict):
            return None
        fill = page_properties.get("pageBackgroundFill")
        if fill is not None:
            if not isinstance(fill, dict):
                return None
            state = fill.get("propertyState")
            if state == "INHERIT":
                continue
            if state not in {None, "RENDERED"}:
                return None
            return _opaque_solid_color(
                fill,
                theme_colors=_theme_colors_from_pages(pages),
            )
    return CURRENT_BRAND_MANIFEST.colors.paper


def _effective_theme_colors(
    presentation: dict[str, Any],
    slide: dict[str, Any],
) -> dict[str, str] | None:
    try:
        return _theme_colors_from_pages(_page_chain(presentation, slide))
    except UnsupportedSourceError:
        return None


def _theme_colors_from_pages(
    pages: tuple[dict[str, Any], ...],
) -> dict[str, str] | None:
    for page in pages:
        page_properties = page.get("pageProperties")
        if page_properties is None:
            continue
        if not isinstance(page_properties, dict):
            return None
        scheme = page_properties.get("colorScheme")
        if scheme is None:
            continue
        return _theme_color_map(scheme)
    return {}


def _theme_color_map(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    colors = value.get("colors")
    if not isinstance(colors, list):
        return None
    result: dict[str, str] = {}
    for pair in colors:
        if not isinstance(pair, dict):
            return None
        theme_type = pair.get("type")
        concrete = _hex_from_rgb(pair.get("color"))
        if (
            not isinstance(theme_type, str)
            or theme_type == "THEME_COLOR_TYPE_UNSPECIFIED"
            or concrete is None
            or theme_type in result
        ):
            return None
        result[theme_type] = concrete
    return result


def _page_chain(
    presentation: dict[str, Any],
    slide: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    properties = slide.get("slideProperties")
    if properties is None:
        return (slide,)
    if not isinstance(properties, dict):
        raise UnsupportedSourceError
    layout_id = properties.get("layoutObjectId")
    if layout_id is None:
        return (slide,)
    if not isinstance(layout_id, str):
        raise UnsupportedSourceError
    layout = _page_by_id(presentation.get("layouts"), SlideObjectId(layout_id))
    layout_properties = layout.get("layoutProperties")
    if not isinstance(layout_properties, dict):
        raise UnsupportedSourceError
    master_id = layout_properties.get("masterObjectId")
    if master_id is None:
        return slide, layout
    if not isinstance(master_id, str):
        raise UnsupportedSourceError
    master = _page_by_id(presentation.get("masters"), SlideObjectId(master_id))
    return slide, layout, master


def _effective_shape_background(
    element: dict[str, Any],
    *,
    ancestors: tuple[dict[str, Any], ...],
    page_background: str | None,
    theme_colors: dict[str, str] | None,
) -> str | None:
    for candidate in (element, *ancestors):
        shape = candidate.get("shape")
        properties = shape.get("shapeProperties") if isinstance(shape, dict) else None
        if not isinstance(properties, dict) or "shapeBackgroundFill" not in properties:
            continue
        fill = properties["shapeBackgroundFill"]
        if not isinstance(fill, dict):
            return None
        state = fill.get("propertyState")
        if state == "INHERIT":
            continue
        if state == "NOT_RENDERED":
            return page_background
        if state not in {None, "RENDERED"}:
            return None
        return _opaque_solid_color(fill, theme_colors=theme_colors)
    return page_background


def _has_visual_underlay(
    presentation: dict[str, Any],
    slide: dict[str, Any],
) -> bool:
    try:
        pages = _page_chain(presentation, slide)
    except UnsupportedSourceError:
        return True
    expected_logo_id = sarj_logo_object_id(SlideId(_required_string(slide, "objectId")))
    object_ids = _object_id_occurrences(presentation)
    for page in pages:
        for element in _walk_elements(page.get("pageElements", [])):
            if element.get("objectId") == expected_logo_id:
                continue
            if any(
                kind in element
                for kind in (
                    "image",
                    "line",
                    "sheetsChart",
                    "speakerSpotlight",
                    "table",
                    "video",
                    "wordArt",
                )
            ):
                return True
            shape = element.get("shape")
            if not isinstance(shape, dict):
                continue
            ancestors = _placeholder_ancestors(shape, object_ids=object_ids)
            if ancestors is None or _shape_has_rendered_fill(element, ancestors=ancestors):
                return True
    return False


def _shape_has_rendered_fill(
    element: dict[str, Any],
    *,
    ancestors: tuple[dict[str, Any], ...],
) -> bool:
    for candidate in (element, *ancestors):
        shape = candidate.get("shape")
        properties = shape.get("shapeProperties") if isinstance(shape, dict) else None
        if not isinstance(properties, dict) or "shapeBackgroundFill" not in properties:
            continue
        fill = properties["shapeBackgroundFill"]
        if not isinstance(fill, dict):
            return True
        state = fill.get("propertyState")
        if state == "INHERIT":
            continue
        if state == "NOT_RENDERED":
            return False
        return state in {None, "RENDERED"}
    return False


def _opaque_solid_color(
    fill: object,
    *,
    theme_colors: dict[str, str] | None,
) -> str | None:
    if not isinstance(fill, dict):
        return None
    solid = fill.get("solidFill")
    if not isinstance(solid, dict):
        return None
    alpha = solid.get("alpha", 1)
    color = solid.get("color")
    if not isinstance(alpha, int | float) or alpha != 1 or not isinstance(color, dict):
        return None
    return _hex_from_opaque_color(color, theme_colors=theme_colors)


def _contrast_ratio(foreground: str, background: str) -> float:
    lighter = max(_relative_luminance(foreground), _relative_luminance(background))
    darker = min(_relative_luminance(foreground), _relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(color: str) -> float:
    channels = tuple(int(color[index : index + 2], 16) / 255 for index in (1, 3, 5))
    linear = tuple(
        channel / 12.92 if channel <= _SRGB_LINEAR_THRESHOLD else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    )
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _logo_requests(
    presentation: dict[str, Any],
    *,
    logo_uri: str,
) -> _LogoPlan:
    if logo_uri != CURRENT_BRAND_MANIFEST.assets.logo_uri:
        return _LogoPlan(
            [],
            {},
            [
                TransformFinding(
                    code="logo_unavailable",
                    detail="Typography was applied, but the pinned Sarj logo was unavailable",
                )
            ],
        )
    slides = _slides(presentation)
    try:
        layout = _logo_layout(presentation)
    except UnsupportedSourceError:
        return _LogoPlan(
            [],
            {},
            [
                _unsafe_geometry_finding(SlideId(_required_string(slide, "objectId")))
                for slide in slides
            ],
        )
    requests: list[dict[str, Any]] = []
    placements: dict[SlideId, _Rect] = {}
    findings: list[TransformFinding] = []
    object_ids = _object_id_occurrences(presentation)
    plan = _LogoPlan(requests, placements, findings)
    for slide in slides:
        _append_slide_logo_plan(
            plan,
            presentation,
            slide,
            logo_uri=logo_uri,
            layout=layout,
            object_ids=object_ids,
        )
    return plan


def _append_slide_logo_plan(
    plan: _LogoPlan,
    presentation: dict[str, JsonValue],
    slide: dict[str, JsonValue],
    *,
    logo_uri: str,
    layout: _LogoLayout,
    object_ids: dict[str, tuple[dict[str, JsonValue], ...]],
) -> None:
    requests, placements, findings = plan
    background = _effective_page_background(presentation, slide)
    if background is None or _contrast_ratio("#000000", background) < _MIN_TEXT_CONTRAST:
        removal = _unsafe_contrast_logo_plan(
            slide,
            logo_uri=logo_uri,
            layout=layout,
            object_ids=object_ids,
        )
        requests.extend(removal.requests)
        findings.extend(removal.findings)
        return
    try:
        inherited_elements = _inherited_page_elements(presentation, slide)
    except UnsupportedSourceError:
        findings.append(_unsafe_geometry_finding(SlideId(_required_string(slide, "objectId"))))
        return
    request, placement, finding = _logo_request_for_slide(
        slide,
        logo_uri=logo_uri,
        layout=layout,
        object_ids=object_ids,
        inherited_elements=inherited_elements,
    )
    if finding is not None:
        findings.append(finding)
    if placement is not None:
        placements[SlideId(_required_string(slide, "objectId"))] = placement
    if request is not None:
        requests.append(request)


def _unsafe_contrast_logo_plan(
    slide: dict[str, JsonValue],
    *,
    logo_uri: str,
    layout: _LogoLayout,
    object_ids: dict[str, tuple[dict[str, JsonValue], ...]],
) -> _LogoPlan:
    removal, finding = _unsafe_contrast_logo_removal(
        slide, logo_uri=logo_uri, layout=layout, object_ids=object_ids
    )
    requests: list[dict[str, JsonValue]] = []
    findings: list[TransformFinding] = []
    if removal is not None:
        requests.append(removal)
    if finding is not None:
        findings.append(finding)
    findings.append(_logo_contrast_finding(SlideId(_required_string(slide, "objectId"))))
    return _LogoPlan(requests, {}, findings)


def _unsafe_contrast_logo_removal(
    slide: dict[str, Any],
    *,
    logo_uri: str,
    layout: _LogoLayout,
    object_ids: dict[str, tuple[dict[str, Any], ...]],
) -> _LogoRemovalDecision:
    slide_id = SlideId(_required_string(slide, "objectId"))
    logo_id = sarj_logo_object_id(slide_id)
    existing = [
        element for element in _top_level_elements(slide) if element.get("objectId") == logo_id
    ]
    occurrences = object_ids.get(logo_id, ())
    if not occurrences:
        return _LogoRemovalDecision(None, None)
    if len(occurrences) != 1 or len(existing) != 1:
        return _LogoRemovalDecision(None, _logo_id_collision_finding(slide_id))
    if not _element_geometry_is_safe(existing[0], layout=layout):
        return _LogoRemovalDecision(None, _unsafe_geometry_finding(slide_id))
    if _owned_logo_bounds(existing[0], logo_uri=logo_uri, layout=layout) is None:
        return _LogoRemovalDecision(None, _logo_id_collision_finding(slide_id))
    return _LogoRemovalDecision({"deleteObject": {"objectId": logo_id}}, None)


def _logo_layout(presentation: dict[str, Any]) -> _LogoLayout:
    page_width, page_height = _page_size(presentation)
    slides = CURRENT_BRAND_MANIFEST.slides
    if not math.isclose(
        page_width / page_height,
        slides.canvas_width / slides.canvas_height,
        rel_tol=_CANVAS_ASPECT_REL_TOLERANCE,
        abs_tol=0,
    ):
        raise UnsupportedSourceError
    asset_width = CURRENT_BRAND_MANIFEST.assets.logo_pixel_width
    asset_height = CURRENT_BRAND_MANIFEST.assets.logo_pixel_height
    header = CURRENT_BRAND_MANIFEST.document.header
    if asset_width is None or asset_height is None or header is None:
        raise UnsupportedSourceError
    logo_scale = min(header.logo_width_pt / asset_width, header.logo_height_pt / asset_height)
    width = asset_width * logo_scale
    height = asset_height * logo_scale
    inset_x = slides.safe_inset * page_width / slides.canvas_width
    inset_y = slides.safe_inset * page_height / slides.canvas_height
    return _LogoLayout(
        page_width=page_width,
        page_height=page_height,
        width=width,
        height=height,
        candidates=(
            _Rect(page_width - inset_x - width, inset_y, width, height),
            _Rect(page_width - inset_x - width, page_height - inset_y - height, width, height),
            _Rect(inset_x, page_height - inset_y - height, width, height),
            _Rect(inset_x, inset_y, width, height),
        ),
    )


def _logo_request_for_slide(
    slide: dict[str, Any],
    *,
    logo_uri: str,
    layout: _LogoLayout,
    object_ids: dict[str, tuple[dict[str, Any], ...]],
    inherited_elements: tuple[dict[str, Any], ...],
) -> _LogoDecision:
    slide_id = SlideId(_required_string(slide, "objectId"))
    logo_id = sarj_logo_object_id(slide_id)
    top_level = _top_level_elements(slide)
    existing = [element for element in top_level if element.get("objectId") == logo_id]
    occurrences = object_ids.get(logo_id, ())
    if occurrences and (len(occurrences) != 1 or len(existing) != 1):
        return _LogoDecision(None, None, _logo_id_collision_finding(slide_id))
    if existing and not _element_geometry_is_safe(existing[0], layout=layout):
        return _LogoDecision(None, None, _unsafe_geometry_finding(slide_id))
    occupied = _occupied_bounds(
        [*top_level, *inherited_elements],
        logo_id=logo_id,
        layout=layout,
    )
    if occupied is None:
        return _LogoDecision(None, None, _unsafe_geometry_finding(slide_id))
    placement = next(
        (
            candidate
            for candidate in layout.candidates
            if not any(_overlaps(candidate, box) for box in occupied)
        ),
        None,
    )
    if placement is None:
        return _LogoDecision(None, None, _logo_collision_finding(slide_id))
    if not _rect_is_safe(
        placement,
        page_width=layout.page_width,
        page_height=layout.page_height,
    ):
        return _LogoDecision(None, None, _unsafe_geometry_finding(slide_id))
    if existing:
        existing_bounds = _owned_logo_bounds(
            existing[0], logo_uri=logo_uri, layout=layout, occupied=occupied
        )
        if existing_bounds is None:
            return _LogoDecision(None, None, _logo_id_collision_finding(slide_id))
        return _LogoDecision(None, existing_bounds, None)
    return _LogoDecision(
        _create_logo_request(
            logo_id=logo_id,
            slide_id=slide_id,
            logo_uri=logo_uri,
            layout=layout,
            placement=placement,
        ),
        placement,
        None,
    )


def _owned_logo_bounds(
    element: dict[str, JsonValue],
    *,
    logo_uri: str,
    layout: _LogoLayout,
    occupied: tuple[_Rect, ...] = (),
) -> _Rect | None:
    bounds = _element_bounds(element)
    if (
        bounds is None
        or not any(_rect_close(bounds, candidate) for candidate in layout.candidates)
        or any(_overlaps(bounds, box) for box in occupied)
        or not _existing_logo_is_exact(element, logo_uri=logo_uri, placement=bounds)
    ):
        return None
    return bounds


def _top_level_elements(slide: dict[str, Any]) -> list[dict[str, Any]]:
    raw_elements = slide.get("pageElements", [])
    if not isinstance(raw_elements, list) or not all(
        isinstance(element, dict) for element in raw_elements
    ):
        raise UnsupportedSourceError
    return [{str(key): value for key, value in element.items()} for element in raw_elements]


def _occupied_bounds(
    elements: list[dict[str, Any]],
    *,
    logo_id: SlideObjectId,
    layout: _LogoLayout,
) -> tuple[_Rect, ...] | None:
    occupied: list[_Rect] = []
    for element in elements:
        if element.get("objectId") == logo_id:
            continue
        bounds = _element_bounds(element)
        if bounds is None or not _rect_is_safe(
            bounds,
            page_width=layout.page_width,
            page_height=layout.page_height,
        ):
            return None
        occupied.append(bounds)
    return tuple(occupied)


def _existing_logo_is_exact(
    element: dict[str, Any],
    *,
    logo_uri: str,
    placement: _Rect,
) -> bool:
    image = element.get("image")
    bounds = _element_bounds(element)
    return (
        isinstance(image, dict)
        and image.get("sourceUrl") == logo_uri
        and _image_appearance_is_default(image.get("imageProperties"))
        and _logo_transform_is_exact(element, placement=placement)
        and bounds is not None
        and _rect_close(bounds, placement)
    )


def _logo_transform_is_exact(element: dict[str, Any], *, placement: _Rect) -> bool:
    size = element.get("size")
    transform = element.get("transform")
    if not isinstance(size, dict) or not isinstance(transform, dict):
        return False
    width = _dimension_points(size.get("width"))
    height = _dimension_points(size.get("height"))
    unit = transform.get("unit", "EMU")
    factor = 1 / _EMU_PER_POINT if unit == "EMU" else 1.0 if unit == "PT" else None
    affine = _transform_values(transform)
    if width is None or height is None or factor is None or affine is None:
        return False
    unscaled = _Rect(
        left=affine.translate_x * factor,
        top=affine.translate_y * factor,
        width=width,
        height=height,
    )
    return (
        math.isclose(affine.scale_x, 1.0, abs_tol=1e-9)
        and math.isclose(affine.scale_y, 1.0, abs_tol=1e-9)
        and math.isclose(affine.shear_x, 0.0, abs_tol=1e-9)
        and math.isclose(affine.shear_y, 0.0, abs_tol=1e-9)
        and _rect_close(unscaled, placement)
    )


def _image_appearance_is_default(raw: object) -> bool:
    if raw is None:
        return True
    if not isinstance(raw, dict):
        return False
    allowed = {"brightness", "contrast", "cropProperties", "outline", "shadow", "transparency"}
    if set(raw) - allowed:
        return False
    if any(not _is_zero(raw.get(key, 0)) for key in ("brightness", "contrast", "transparency")):
        return False
    crop = raw.get("cropProperties")
    if crop is not None and (
        not isinstance(crop, dict)
        or any(
            not _is_zero(crop.get(key, 0))
            for key in ("angle", "bottomOffset", "leftOffset", "rightOffset", "topOffset")
        )
    ):
        return False
    return all(_effect_is_not_rendered(raw.get(key)) for key in ("outline", "shadow"))


def _effect_is_not_rendered(raw: object) -> bool:
    return raw is None or (
        isinstance(raw, dict)
        and set(raw) <= {"propertyState"}
        and raw.get("propertyState") in {None, "NOT_RENDERED"}
    )


def _is_zero(value: object) -> bool:
    return isinstance(value, int | float) and math.isfinite(value) and value == 0


def _inherited_page_elements(
    presentation: dict[str, Any],
    slide: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        element
        for page in _page_chain(presentation, slide)[1:]
        for element in _top_level_elements(page)
        if not _is_placeholder_template(element)
    )


def _is_placeholder_template(element: dict[str, Any]) -> bool:
    shape = element.get("shape")
    return isinstance(shape, dict) and isinstance(shape.get("placeholder"), dict)


def _page_by_id(raw: object, object_id: SlideObjectId) -> dict[str, JsonValue]:
    if not isinstance(raw, list):
        raise UnsupportedSourceError
    matches = [page for page in raw if isinstance(page, dict) and page.get("objectId") == object_id]
    if len(matches) != 1:
        raise UnsupportedSourceError
    return {str(key): value for key, value in matches[0].items()}


def _element_geometry_is_safe(element: dict[str, Any], *, layout: _LogoLayout) -> bool:
    bounds = _element_bounds(element)
    return bounds is not None and _rect_is_safe(
        bounds,
        page_width=layout.page_width,
        page_height=layout.page_height,
    )


def _create_logo_request(
    *,
    logo_id: SlideObjectId,
    slide_id: SlideId,
    logo_uri: str,
    layout: _LogoLayout,
    placement: _Rect,
) -> dict[str, Any]:
    return {
        "createImage": {
            "objectId": logo_id,
            "url": logo_uri,
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {
                    "width": {"magnitude": layout.width, "unit": "PT"},
                    "height": {"magnitude": layout.height, "unit": "PT"},
                },
                "transform": {
                    "scaleX": 1,
                    "scaleY": 1,
                    "translateX": placement.left,
                    "translateY": placement.top,
                    "unit": "PT",
                },
            },
        }
    }


def _expected_brand_sha256(
    *,
    style_targets: tuple[_StyleTarget, ...],
    logo_rects: dict[SlideId, _Rect],
) -> str:
    styles = [_expected_style(target) for target in style_targets]
    logos = [
        _canonical_logo(
            object_id=sarj_logo_object_id(slide_id),
            slide_id=slide_id,
            rect=rect,
        )
        for slide_id, rect in logo_rects.items()
    ]
    return _brand_digest(styles=styles, logos=logos)


def _expected_style(target: _StyleTarget) -> dict[str, Any]:
    desired, _ = _desired_style(
        placeholder_type=target.placeholder_type,
        font_weight=target.runs[0].font_weight,
    )
    branded = _canonical_style(desired)
    return _canonical_style_target(target, brand_style_json=_canonical_json(branded))


def _observed_style(target: _StyleTarget) -> dict[str, Any]:
    return _canonical_style_target(target, brand_style_json=None)


def _canonical_style_target(
    target: _StyleTarget,
    *,
    brand_style_json: str | None,
) -> dict[str, Any]:
    return {
        "objectId": target.object_id,
        "runs": [
            {
                "brand": json.loads(brand_style_json or run.brand_style_json),
                "ordinal": run.ordinal,
                "preserved": json.loads(run.preserved_style_json),
            }
            for run in target.runs
        ],
    }


def _canonical_style(
    style: dict[str, Any],
    *,
    theme_colors: dict[str, str] | None = None,
) -> dict[str, Any]:
    family = style.get("weightedFontFamily")
    size = style.get("fontSize")
    foreground = style.get("foregroundColor")
    opaque = foreground.get("opaqueColor") if isinstance(foreground, dict) else None
    result: dict[str, Any] = {
        "fontFamily": family.get("fontFamily") if isinstance(family, dict) else None,
        "fontWeight": family.get("weight", 400) if isinstance(family, dict) else None,
        "fontSizePt": _dimension_points(size),
        "color": _hex_from_opaque_color(opaque, theme_colors=theme_colors),
    }
    return result


def _observed_logos(presentation: dict[str, Any]) -> list[dict[str, Any]]:
    logos: list[dict[str, Any]] = []
    page_width, page_height = _page_size(presentation)
    for slide in _slides(presentation):
        slide_id = SlideId(_required_string(slide, "objectId"))
        expected_id = sarj_logo_object_id(slide_id)
        for element in slide.get("pageElements", []):
            if not isinstance(element, dict):
                raise UnsupportedSourceError
            object_id = element.get("objectId")
            if object_id != expected_id:
                continue
            bounds = _element_bounds(element)
            image = element.get("image")
            if (
                bounds is None
                or not _rect_is_safe(
                    bounds,
                    page_width=page_width,
                    page_height=page_height,
                )
                or not isinstance(image, dict)
                or image.get("sourceUrl") != CURRENT_BRAND_MANIFEST.assets.logo_uri
                or not _image_appearance_is_default(image.get("imageProperties"))
            ):
                raise UnsupportedSourceError
            logos.append(_canonical_logo(object_id=expected_id, slide_id=slide_id, rect=bounds))
    return logos


def _canonical_logo(
    *, object_id: SlideObjectId, slide_id: SlideId, rect: _Rect
) -> dict[str, JsonValue]:
    return {
        "objectId": object_id,
        "slideId": slide_id,
        "sourceUrl": CURRENT_BRAND_MANIFEST.assets.logo_uri,
        "assetSha256": CURRENT_BRAND_MANIFEST.assets.logo_sha256,
        "leftPt": round(rect.left, 3),
        "topPt": round(rect.top, 3),
        "widthPt": round(rect.width, 3),
        "heightPt": round(rect.height, 3),
    }


def _brand_digest(*, styles: list[dict[str, Any]], logos: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        {"logos": logos, "styles": styles},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _slides(presentation: dict[str, Any]) -> list[dict[str, Any]]:
    slides = presentation.get("slides")
    if not isinstance(slides, list):
        raise UnsupportedSourceError
    if not all(isinstance(slide, dict) for slide in slides):
        raise UnsupportedSourceError
    return [{str(key): value for key, value in slide.items()} for slide in slides]


def _walk_elements(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise UnsupportedSourceError
    result: list[dict[str, Any]] = []
    pending = deque(raw)
    while pending:
        candidate = pending.popleft()
        if not isinstance(candidate, dict):
            raise UnsupportedSourceError
        element = {str(key): value for key, value in candidate.items()}
        result.append(element)
        group = element.get("elementGroup")
        if isinstance(group, dict):
            children = group.get("children", [])
            if not isinstance(children, list):
                raise UnsupportedSourceError
            pending.extendleft(reversed(children))
    return result


def _page_size(presentation: dict[str, Any]) -> _PageSize:
    size = presentation.get("pageSize")
    if not isinstance(size, dict):
        raise UnsupportedSourceError
    width = _dimension_points(size.get("width"))
    height = _dimension_points(size.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        raise UnsupportedSourceError
    return _PageSize(width, height)


def _element_bounds(element: dict[str, Any]) -> _Rect | None:
    size = element.get("size")
    transform = element.get("transform")
    if not isinstance(size, dict) or not isinstance(transform, dict):
        return None
    width = _dimension_points(size.get("width"))
    height = _dimension_points(size.get("height"))
    unit = transform.get("unit", "EMU")
    factor = 1 / _EMU_PER_POINT if unit == "EMU" else 1.0 if unit == "PT" else None
    if width is None or height is None or factor is None:
        return None
    affine = _transform_values(transform)
    if affine is None:
        return None
    translated_x = affine.translate_x * factor
    translated_y = affine.translate_y * factor
    corners = (
        (translated_x, translated_y),
        (
            translated_x + affine.scale_x * width,
            translated_y + affine.shear_y * width,
        ),
        (
            translated_x + affine.shear_x * height,
            translated_y + affine.scale_y * height,
        ),
        (
            translated_x + affine.scale_x * width + affine.shear_x * height,
            translated_y + affine.shear_y * width + affine.scale_y * height,
        ),
    )
    xs = [point[0] for point in corners]
    ys = [point[1] for point in corners]
    return _Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _transform_values(transform: dict[str, Any]) -> _Affine | None:
    values = (
        transform.get("scaleX", 1),
        transform.get("scaleY", 1),
        transform.get("shearX", 0),
        transform.get("shearY", 0),
        transform.get("translateX", 0),
        transform.get("translateY", 0),
    )
    if not all(isinstance(value, int | float) and math.isfinite(value) for value in values):
        return None
    return _Affine(*map(float, values))


def _dimension_points(value: object) -> float | None:
    if not isinstance(value, dict):
        return None
    magnitude = value.get("magnitude")
    unit = value.get("unit")
    if not isinstance(magnitude, int | float) or not math.isfinite(magnitude):
        return None
    if unit == "PT":
        return round(float(magnitude), 3)
    if unit == "EMU":
        return round(float(magnitude) / _EMU_PER_POINT, 3)
    return None


def _overlaps(first: _Rect, second: _Rect) -> bool:
    return not (
        first.left + first.width <= second.left
        or second.left + second.width <= first.left
        or first.top + first.height <= second.top
        or second.top + second.height <= first.top
    )


def _rect_is_safe(rect: _Rect, *, page_width: float, page_height: float) -> bool:
    values = (rect.left, rect.top, rect.width, rect.height, page_width, page_height)
    return (
        all(math.isfinite(value) for value in values)
        and rect.width > 0
        and rect.height > 0
        and rect.left >= 0
        and rect.top >= 0
        and rect.left + rect.width <= page_width
        and rect.top + rect.height <= page_height
    )


def _rect_close(first: _Rect, second: _Rect) -> bool:
    tolerance = CURRENT_BRAND_MANIFEST.document.header
    max_delta = 0.25 if tolerance is None else tolerance.logo_size_tolerance_pt
    return all(
        abs(left - right) <= max_delta
        for left, right in (
            (first.left, second.left),
            (first.top, second.top),
            (first.width, second.width),
            (first.height, second.height),
        )
    )


def _object_id_occurrences(value: object) -> dict[str, tuple[dict[str, Any], ...]]:
    found: dict[str, list[dict[str, Any]]] = {}

    def visit(candidate: object) -> None:
        if isinstance(candidate, list):
            for child in candidate:
                visit(child)
            return
        if not isinstance(candidate, dict):
            return
        typed_candidate = {str(key): child for key, child in candidate.items()}
        object_id = typed_candidate.get("objectId")
        if isinstance(object_id, str):
            found.setdefault(object_id, []).append(typed_candidate)
        for child in typed_candidate.values():
            visit(child)

    visit(value)
    return {object_id: tuple(elements) for object_id, elements in found.items()}


def _unsafe_geometry_finding(slide_id: SlideId) -> TransformFinding:
    return TransformFinding(
        code="slide_geometry_unsafe",
        block_id=slide_id,
        detail="The Sarj logo was skipped because page-element geometry was not exactly safe",
    )


def _logo_id_collision_finding(slide_id: SlideId) -> TransformFinding:
    return TransformFinding(
        code="slide_logo_id_collision",
        block_id=slide_id,
        detail="The Sarj logo was skipped because its deterministic object ID was already in use",
    )


def _logo_collision_finding(slide_id: SlideId) -> TransformFinding:
    return TransformFinding(
        code="slide_logo_collision",
        block_id=slide_id,
        detail="The Sarj logo was skipped because every safe corner was occupied",
    )


def _logo_contrast_finding(slide_id: SlideId) -> TransformFinding:
    return TransformFinding(
        code="slide_logo_contrast_unresolved",
        block_id=slide_id,
        detail="The black Sarj logo was skipped because background contrast could not be proven",
    )


def _small_text_finding(object_id: SlideObjectId) -> TransformFinding:
    return TransformFinding(
        code="slide_text_too_small",
        block_id=object_id,
        detail=(
            "Long narrative text below 12 pt was preserved unchanged and flagged for visual review"
        ),
    )


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise UnsupportedSourceError from error


def _rgb(hex_color: str) -> dict[str, float]:
    return {
        channel: int(hex_color[index : index + 2], 16) / 255
        for channel, index in (("red", 1), ("green", 3), ("blue", 5))
    }


def _hex_from_rgb(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    channels: list[float] = []
    for name in ("red", "green", "blue"):
        channel = value.get(name, 0)
        if (
            isinstance(channel, bool)
            or not isinstance(channel, int | float)
            or not math.isfinite(channel)
            or not 0 <= channel <= 1
        ):
            return None
        channels.append(float(channel))
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in channels)


def _hex_from_opaque_color(
    value: object,
    *,
    theme_colors: dict[str, str] | None,
) -> str | None:
    if not isinstance(value, dict):
        return None
    color = {str(key): child for key, child in value.items()}
    has_rgb = "rgbColor" in color
    has_theme = "themeColor" in color
    if has_rgb == has_theme:
        return None
    if has_rgb:
        return _hex_from_rgb(color.get("rgbColor"))
    theme = color.get("themeColor")
    if not isinstance(theme, str) or theme_colors is None:
        return None
    return theme_colors.get(theme)


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise UnsupportedSourceError
    return value
