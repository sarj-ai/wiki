from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
from operator import itemgetter
from typing import Any, NamedTuple

from app.document_transform.brand_manifest import CURRENT_BRAND_MANIFEST
from app.document_transform.errors import SourceTooLargeError, UnsupportedSourceError
from app.document_transform.ir import DocumentIR, TextBlock, utf16_length
from app.document_transform.presentation_gateway import PresentationFingerprints
from app.document_transform.schemas import TransformFinding, TransformMode
from app.document_transform.slides_ir import (
    ParsedPresentation,
    SlideTextLocator,
    parse_google_presentation,
    sarj_logo_object_id,
)


MAX_SLIDES_MUTATION_REQUESTS = 1_000
MAX_SLIDES_MUTATION_BYTES = 1_000_000
_EMU_PER_POINT = 12_700
_PRESERVED_TEXT_STYLE_FIELDS = (
    "baselineOffset",
    "bold",
    "italic",
    "link",
    "smallCaps",
    "strikethrough",
    "underline",
)
_PLAIN_TEXT_ELEMENT_COUNT = 2


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
    preserved_style_json: str
    brand_style_json: str


@dataclass(frozen=True, slots=True)
class _StyleTarget:
    object_id: str
    placeholder_type: str | None
    slide_index: int
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


class _ValidatedTextRun(NamedTuple):
    content: str
    style: dict[str, Any]


class _LogoPlan(NamedTuple):
    requests: list[dict[str, Any]]
    placements: dict[str, _Rect]
    findings: list[TransformFinding]


class _LogoDecision(NamedTuple):
    request: dict[str, Any] | None
    placement: _Rect | None
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
    transformed_by_id = {block.id: block for block in transformed.blocks}
    requests: list[dict[str, Any]] = []
    for block in reversed(parsed.document.blocks):
        locator = parsed.locators[block.id]
        replacement = transformed_by_id[block.id].text
        if replacement != block.text:
            requests.extend(_text_delta_requests(block, replacement=replacement, locator=locator))

    findings: list[TransformFinding] = []
    style_targets: tuple[_StyleTarget, ...] = ()
    logo_rects: dict[str, _Rect] = {}
    if mode is TransformMode.UNSLOP:
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
                object_id=str(locator.object_id),
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
                object_id=str(locator.object_id),
                absolute_start=locator.start_index + offsets[source_index],
                absolute_end=locator.start_index + offsets[source_index + 1],
                target_character=target_character,
            )
        source_index -= 1
    if source_index >= 0:
        _append_delete_delta(
            deltas,
            object_id=str(locator.object_id),
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
    object_id: str,
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
    object_id: str,
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


def _delete_text_request(object_id: str, start: int, end: int) -> dict[str, Any]:
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
            slide_index=target.slide_index,
        )
        requests.append(
            {
                "updateTextStyle": {
                    "objectId": target.object_id,
                    "style": style,
                    "textRange": {"type": "ALL"},
                    "fields": fields,
                }
            }
        )
    return _StylePlan(requests, targets, findings)


def _desired_style(
    *,
    placeholder_type: str | None,
    slide_index: int,
) -> _DesiredTextStyle:
    manifest = CURRENT_BRAND_MANIFEST
    slides = manifest.slides
    colors = manifest.colors
    title = placeholder_type in {"TITLE", "CENTERED_TITLE"}
    subtitle = placeholder_type == "SUBTITLE"
    caption = placeholder_type in {"DATE_AND_TIME", "FOOTER", "HEADER", "SLIDE_NUMBER"}
    size = slides.deck_title_size_pt if title and slide_index == 0 else slides.title_size_pt
    if not title:
        size = slides.caption_size_pt if caption else slides.body_size_pt
    color = colors.primary_dark if title else colors.muted if subtitle or caption else colors.ink
    style: dict[str, Any] = {
        "weightedFontFamily": {"fontFamily": manifest.document.font_family},
        "fontSize": {"magnitude": size, "unit": "PT"},
        "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(color)}},
    }
    return _DesiredTextStyle(style, "weightedFontFamily,fontSize,foregroundColor")


def _style_targets(
    presentation: dict[str, Any],
) -> _StyleScan:
    targets: list[_StyleTarget] = []
    findings: list[TransformFinding] = []
    for slide_index, slide in enumerate(_slides(presentation)):
        for element in _walk_elements(slide.get("pageElements", [])):
            target, finding = _style_target(element, slide_index=slide_index)
            if target is not None:
                targets.append(target)
            if finding is not None:
                findings.append(finding)
    return _StyleScan(tuple(targets), findings)


def _style_target(
    element: dict[str, Any],
    *,
    slide_index: int,
) -> _StyleDecision:
    object_id = _required_string(element, "objectId")
    shape = element.get("shape")
    if not isinstance(shape, dict):
        finding = _unsupported_text_finding(object_id) if _has_visible_text(element) else None
        return _StyleDecision(None, finding)
    text = shape.get("text")
    if text is None:
        return _StyleDecision(None, None)
    if not isinstance(text, dict) or not isinstance(text.get("textElements"), list):
        return _StyleDecision(None, _unsupported_text_finding(object_id))
    text_elements = text["textElements"]
    if len(text_elements) != _PLAIN_TEXT_ELEMENT_COUNT:
        return _StyleDecision(None, _unsupported_text_finding(object_id))
    paragraph_element, run_element = text_elements
    if not isinstance(paragraph_element, dict) or not isinstance(run_element, dict):
        return _StyleDecision(None, _unsupported_text_finding(object_id))
    paragraph = paragraph_element.get("paragraphMarker")
    if not isinstance(paragraph, dict) or paragraph.get("bullet") is not None:
        return _StyleDecision(None, _unsupported_text_finding(object_id))
    if not isinstance(run_element.get("textRun"), dict):
        return _StyleDecision(None, _unsupported_text_finding(object_id))
    runs: list[_StyleRun] = []
    for text_element in text_elements:
        if not isinstance(text_element, dict):
            return _StyleDecision(None, _unsupported_text_finding(object_id))
        run = text_element.get("textRun")
        if run is None:
            if "paragraphMarker" in text_element:
                continue
            if _has_visible_text(text_element):
                return _StyleDecision(None, _unsupported_text_finding(object_id))
            continue
        if not isinstance(run, dict):
            return _StyleDecision(None, _unsupported_text_finding(object_id))
        content = run.get("content")
        start = text_element.get("startIndex")
        end = text_element.get("endIndex")
        style = run.get("style")
        validated = _validated_text_run(content=content, start=start, end=end, style=style)
        if validated is None:
            return _StyleDecision(None, _unresolved_style_finding(object_id))
        content, style = validated
        if not content.endswith("\n") or "\n" in content[:-1] or "\r" in content:
            return _StyleDecision(None, _unsupported_text_finding(object_id))
        if style.get("link") is not None:
            return _StyleDecision(None, _unsupported_text_finding(object_id))
        if not content.strip():
            continue
        runs.append(
            _StyleRun(
                ordinal=len(runs),
                preserved_style_json=_canonical_json(
                    {key: style[key] for key in _PRESERVED_TEXT_STYLE_FIELDS if key in style}
                ),
                brand_style_json=_canonical_json(_canonical_style(style)),
            )
        )
    if not runs:
        return _StyleDecision(None, None)
    return _StyleDecision(
        _StyleTarget(
            object_id=object_id,
            placeholder_type=_placeholder_type(shape),
            slide_index=slide_index,
            runs=tuple(runs),
        ),
        None,
    )


def _placeholder_type(shape: dict[str, Any]) -> str | None:
    placeholder = shape.get("placeholder")
    value = placeholder.get("type") if isinstance(placeholder, dict) else None
    if value is not None and not isinstance(value, str):
        raise UnsupportedSourceError
    return value


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


def _unsupported_text_finding(object_id: str) -> TransformFinding:
    return TransformFinding(
        code="slide_text_target_unsupported",
        block_id=object_id,
        detail="Visible text was preserved but not restyled because its native target is unsupported",
    )


def _unresolved_style_finding(object_id: str) -> TransformFinding:
    return TransformFinding(
        code="slide_text_style_unresolved",
        block_id=object_id,
        detail="Visible text was preserved but not restyled because its style was not explicit",
    )


def _validated_text_run(
    *,
    content: object,
    start: object,
    end: object,
    style: object,
) -> _ValidatedTextRun | None:
    if not isinstance(content, str) or not isinstance(style, dict):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if end <= start or end - start != utf16_length(content):
        return None
    return _ValidatedTextRun(content, {str(key): value for key, value in style.items()})


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
            [_unsafe_geometry_finding(_required_string(slide, "objectId")) for slide in slides],
        )
    requests: list[dict[str, Any]] = []
    placements: dict[str, _Rect] = {}
    findings: list[TransformFinding] = []
    object_ids = _object_id_occurrences(presentation)
    for slide in slides:
        try:
            inherited_elements = _inherited_page_elements(presentation, slide)
        except UnsupportedSourceError:
            findings.append(_unsafe_geometry_finding(_required_string(slide, "objectId")))
            continue
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
            placements[_required_string(slide, "objectId")] = placement
        if request is not None:
            requests.append(request)
    return _LogoPlan(requests, placements, findings)


def _logo_layout(presentation: dict[str, Any]) -> _LogoLayout:
    page_width, page_height = _page_size(presentation)
    asset_width = CURRENT_BRAND_MANIFEST.assets.logo_pixel_width
    asset_height = CURRENT_BRAND_MANIFEST.assets.logo_pixel_height
    if asset_width is None or asset_height is None:
        raise UnsupportedSourceError
    width = 52.0
    height = width * asset_height / asset_width
    inset = float(CURRENT_BRAND_MANIFEST.slides.safe_inset)
    return _LogoLayout(
        page_width=page_width,
        page_height=page_height,
        width=width,
        height=height,
        candidates=(
            _Rect(page_width - inset - width, inset, width, height),
            _Rect(page_width - inset - width, page_height - inset - height, width, height),
            _Rect(inset, page_height - inset - height, width, height),
            _Rect(inset, inset, width, height),
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
    slide_id = _required_string(slide, "objectId")
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
        if not _existing_logo_is_exact(existing[0], logo_uri=logo_uri, placement=placement):
            return _LogoDecision(None, None, _logo_id_collision_finding(slide_id))
        return _LogoDecision(None, placement, None)
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
    logo_id: str,
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
        and bounds is not None
        and _rect_close(bounds, placement)
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
    properties = slide.get("slideProperties")
    if properties is None:
        return ()
    if not isinstance(properties, dict):
        raise UnsupportedSourceError
    layout_id = properties.get("layoutObjectId")
    if layout_id is None:
        return ()
    if not isinstance(layout_id, str):
        raise UnsupportedSourceError
    layout = _page_by_id(presentation.get("layouts"), layout_id)
    inherited = list(_top_level_elements(layout))
    layout_properties = layout.get("layoutProperties")
    if not isinstance(layout_properties, dict):
        raise UnsupportedSourceError
    master_id = layout_properties.get("masterObjectId")
    if master_id is None:
        return tuple(inherited)
    if not isinstance(master_id, str):
        raise UnsupportedSourceError
    inherited.extend(_top_level_elements(_page_by_id(presentation.get("masters"), master_id)))
    return tuple(inherited)


def _page_by_id(raw: object, object_id: str) -> dict[str, Any]:
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
    logo_id: str,
    slide_id: str,
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
    logo_rects: dict[str, _Rect],
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
        slide_index=target.slide_index,
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


def _canonical_style(style: dict[str, Any]) -> dict[str, Any]:
    family = style.get("weightedFontFamily")
    size = style.get("fontSize")
    foreground = style.get("foregroundColor")
    opaque = foreground.get("opaqueColor") if isinstance(foreground, dict) else None
    rgb = opaque.get("rgbColor") if isinstance(opaque, dict) else None
    result: dict[str, Any] = {
        "fontFamily": family.get("fontFamily") if isinstance(family, dict) else None,
        "fontSizePt": _dimension_points(size),
        "color": _hex_from_rgb(rgb),
    }
    return result


def _observed_logos(presentation: dict[str, Any]) -> list[dict[str, Any]]:
    logos: list[dict[str, Any]] = []
    page_width, page_height = _page_size(presentation)
    for slide in _slides(presentation):
        slide_id = _required_string(slide, "objectId")
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


def _canonical_logo(*, object_id: str, slide_id: str, rect: _Rect) -> dict[str, Any]:
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


def _unsafe_geometry_finding(slide_id: str) -> TransformFinding:
    return TransformFinding(
        code="slide_geometry_unsafe",
        block_id=slide_id,
        detail="The Sarj logo was skipped because page-element geometry was not exactly safe",
    )


def _logo_id_collision_finding(slide_id: str) -> TransformFinding:
    return TransformFinding(
        code="slide_logo_id_collision",
        block_id=slide_id,
        detail="The Sarj logo was skipped because its deterministic object ID was already in use",
    )


def _logo_collision_finding(slide_id: str) -> TransformFinding:
    return TransformFinding(
        code="slide_logo_collision",
        block_id=slide_id,
        detail="The Sarj logo was skipped because every safe corner was occupied",
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
    red = value.get("red")
    green = value.get("green")
    blue = value.get("blue")
    if (
        not isinstance(red, int | float)
        or not isinstance(green, int | float)
        or not isinstance(blue, int | float)
    ):
        return None
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in (red, green, blue))


def _required_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise UnsupportedSourceError
    return value
