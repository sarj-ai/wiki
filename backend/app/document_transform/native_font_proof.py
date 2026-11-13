from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Annotated, Any, Literal, NewType, assert_never

from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictBool,
    field_validator,
    model_validator,
)

from app.document_transform.errors import UnsupportedSourceError
from app.document_transform.ir import (
    MAX_DOCUMENT_BLOCKS,
    BlockKind,
    DocumentIR,
    TextBlock,
    parse_google_document,
    utf16_length,
)
from app.document_transform.native_font_semantics import (
    NativeFontSemantics,
    capture_font_semantics,
    semantic_font_digest,
)
from app.identifiers import DocsTabId


_MAX_COMPONENT_BYTES = 131072
NativeFontBlockId = NewType("NativeFontBlockId", str)
MAX_NATIVE_FONT_INTERVALS = 5000
_COMPONENT_ERRORS = (
    KeyError,
    IndexError,
    TypeError,
    ValueError,
    AttributeError,
    OverflowError,
    RecursionError,
)


class NativeFontComponents(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    paragraph_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    visible_residual_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_residual_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_family_representation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _NativeFontProofBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tab_id: DocsTabId
    body_ordinal: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    named_styles_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    components: NativeFontComponents | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_coerced_version(cls, value: object) -> object:
        if isinstance(value, dict) and "version" in value:
            version = value["version"]
            if type(version) is not int or version not in {1, 2}:
                raise ValueError
        return value


class LegacyNativeFontProof(_NativeFontProofBinding):
    version: Literal[1] = 1
    semantics: None = None
    block_id: None = None
    visual_authority: Literal[False] = False

    @field_validator("visual_authority", mode="before")
    @classmethod
    def reject_coerced_visual_authority(cls, value: JsonValue) -> Literal[False]:
        if value is not False:
            raise ValueError
        return False


class SemanticNativeFontProof(_NativeFontProofBinding):
    version: Literal[2] = 2
    semantics: NativeFontSemantics
    block_id: NativeFontBlockId = Field(pattern=r"^b[0-9]+$", max_length=32)
    visual_authority: StrictBool = False


type NativeFontProof = Annotated[
    LegacyNativeFontProof | SemanticNativeFontProof, Field(discriminator="version")
]


class NativeFontProofPayload(RootModel[NativeFontProof]):
    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True)
class NativeVisualAuthoritySelection:
    block_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativeFontVerificationPolicy:
    preserved_family_block_ids: tuple[str, ...] = ()
    require_target_family: bool = False


def _bodies(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            tab_id = tab["tabProperties"]["tabId"]
            if tab_id in result:
                raise ValueError
            result[tab_id] = tab["documentTab"].get("body", {}).get("content", [])
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    return result


def native_paragraph_font_sha256(
    element: dict[str, Any], *, preserve_font_family: bool = False
) -> str:
    payload = deepcopy(element)
    start, end = payload.pop("startIndex"), payload.pop("endIndex")
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise ValueError
    paragraph = payload["paragraph"]
    parts = paragraph["elements"]
    if not isinstance(parts, list) or not parts:
        raise ValueError
    canonical: list[dict[str, Any]] = []
    cursor = start
    for part in parts:
        run_start, run_end = part.pop("startIndex"), part.pop("endIndex")
        run = part["textRun"]
        content = run.pop("content")
        if not isinstance(content, str) or not content:
            raise ValueError
        if (
            type(run_start) is not int
            or type(run_end) is not int
            or run_start != cursor
            or run_end != cursor + utf16_length(content)
        ):
            raise ValueError
        cursor = run_end
        suffix = deepcopy(part) if run_end == end else None
        style = run.get("textStyle", {})
        if not isinstance(style, dict):
            raise TypeError
        family = style.get("weightedFontFamily", {})
        if not isinstance(family, dict):
            raise TypeError
        if "fontFamily" in family and (
            not isinstance(family["fontFamily"], str) or not family["fontFamily"]
        ):
            raise ValueError
        if not preserve_font_family:
            family.pop("fontFamily", None)
            if not family:
                style.pop("weightedFontFamily", None)
            if not style:
                run.pop("textStyle", None)
        if suffix is not None:
            if not content.endswith("\n"):
                raise ValueError
            _append_run(canonical, content[:-1], part)
            # The paragraph terminator is outside the authorized font ranges.
            _append_run(canonical, "\n", suffix)
        else:
            _append_run(canonical, content, part)
    if cursor != end:
        raise ValueError
    paragraph["elements"] = canonical
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _append_run(canonical: list[dict[str, Any]], content: str, properties: dict[str, Any]) -> None:
    if not content:
        return
    # Only exactly equal residual payloads may be coalesced. Unknown fields,
    # links, suggestions, weights and all non-family styles remain bound.
    if canonical and canonical[-1]["properties"] == properties:
        canonical[-1]["content"] += content
    else:
        canonical.append({"content": content, "properties": properties})


def _named_styles_digest(document: dict[str, Any], tab_id: str) -> str:
    matches = []

    def visit(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            if tab["tabProperties"]["tabId"] == tab_id:
                matches.append(tab["documentTab"].get("namedStyles"))
            visit(tab.get("childTabs", []))

    visit(document.get("tabs", []))
    if len(matches) != 1:
        raise ValueError
    return hashlib.sha256(
        json.dumps(
            [document.get("namedStyles"), matches[0]],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def capture_native_font_proof(
    document: dict[str, Any] | None,
    original: DocumentIR,
    *,
    preserved_family_block_ids: tuple[str, ...] = (),
    target_font_family: str | None = None,
    transformed: DocumentIR | None = None,
    visual_authority: NativeVisualAuthoritySelection | None = None,
    visual_authority_only: bool = False,
) -> tuple[NativeFontProof, ...]:
    if document is None:
        raise ValueError
    visual_authority_block_ids = visual_authority.block_ids if visual_authority else ()
    bodies = _bodies(document)
    result = []
    captured_ids = set()
    interval_count = 0
    position_deltas = (
        _position_deltas(original, transformed) if target_font_family is not None else {}
    )
    for block in original.blocks:
        selected_visual = block.id in visual_authority_block_ids
        if visual_authority_only and not selected_visual:
            continue
        if not _needs_font_proof(
            block,
            selected_visual=selected_visual,
            transformed=transformed,
            target_font_family=target_font_family,
        ):
            continue
        proof = _capture_block_font_proof(
            document,
            bodies,
            block,
            target_font_family=target_font_family,
            preserve_family=block.id in preserved_family_block_ids,
            position_delta=position_deltas.get(block.id, 0),
            selected_visual=selected_visual,
        )
        match proof:
            case SemanticNativeFontProof():
                interval_count += len(proof.semantics.intervals)
                if interval_count > MAX_NATIVE_FONT_INTERVALS:
                    raise ValueError
            case LegacyNativeFontProof():
                pass
            case _:
                assert_never(proof)
        captured_ids.add(block.id)
        result.append(proof)
    _require_font_proof_coverage(
        captured_ids,
        NativeFontVerificationPolicy(preserved_family_block_ids=preserved_family_block_ids),
        NativeVisualAuthoritySelection(visual_authority_block_ids),
        target_font_family,
    )
    return tuple(result)


def _require_font_proof_coverage(
    captured_ids: set[str],
    font_policy: NativeFontVerificationPolicy,
    visual_authority: NativeVisualAuthoritySelection,
    target_font_family: str | None,
) -> None:
    if (
        len(font_policy.preserved_family_block_ids)
        != len(set(font_policy.preserved_family_block_ids))
        or not set(font_policy.preserved_family_block_ids) <= captured_ids
        or (font_policy.preserved_family_block_ids and target_font_family is None)
    ):
        raise ValueError
    if len(visual_authority.block_ids) != len(set(visual_authority.block_ids)) or not set(
        visual_authority.block_ids
    ) <= set(font_policy.preserved_family_block_ids):
        raise ValueError


def _capture_block_font_proof(
    document: dict[str, JsonValue],
    bodies: dict[str, list[dict[str, JsonValue]]],
    block: TextBlock,
    *,
    target_font_family: str | None,
    preserve_family: bool,
    position_delta: int,
    selected_visual: bool,
) -> NativeFontProof:
    source = block.source_range
    if source is None:
        raise ValueError
    matches = [
        (ordinal, element)
        for ordinal, element in enumerate(bodies[source.tab_id])
        if element.get("startIndex") == source.start_index
        and element.get("endIndex") == source.end_index + 1
    ]
    if len(matches) != 1:
        raise ValueError
    ordinal, element = matches[0]
    digest = native_paragraph_font_sha256(element)
    semantics = None
    if target_font_family is not None:
        semantics = capture_font_semantics(
            document,
            source.tab_id,
            element,
            target_font_family=target_font_family,
            preserve_family=preserve_family,
            position_delta=position_delta,
        )
        digest = semantic_font_digest(
            document, source.tab_id, element, semantics, require_target_family=False
        )
    binding = _NativeFontProofBinding(
        tab_id=source.tab_id,
        body_ordinal=ordinal,
        sha256=digest,
        named_styles_sha256=_named_styles_digest(document, source.tab_id),
        components=_optional_components(element),
    )
    if semantics is not None:
        return SemanticNativeFontProof(
            tab_id=binding.tab_id,
            body_ordinal=binding.body_ordinal,
            sha256=binding.sha256,
            named_styles_sha256=binding.named_styles_sha256,
            components=binding.components,
            block_id=NativeFontBlockId(block.id),
            semantics=semantics,
            visual_authority=selected_visual,
        )
    if selected_visual:
        raise ValueError
    return LegacyNativeFontProof(
        tab_id=binding.tab_id,
        body_ordinal=binding.body_ordinal,
        sha256=binding.sha256,
        named_styles_sha256=binding.named_styles_sha256,
        components=binding.components,
    )


def _needs_font_proof(
    block: TextBlock,
    *,
    selected_visual: bool,
    transformed: DocumentIR | None,
    target_font_family: str | None,
) -> bool:
    if block.source_range is None:
        return False
    if selected_visual or (not block.editable and block.font_style_ranges):
        return True
    if block.protected_suffix is None or transformed is None or target_font_family is None:
        return False
    # Linked prose receives only a font-family change. When its wording is
    # unchanged, bind all citation styling, link targets and paragraph metadata
    # with the same strict native proof used by other font-only paragraphs.
    return any(after.id == block.id and after.text == block.text for after in transformed.blocks)


def _position_deltas(original: DocumentIR, transformed: DocumentIR | None) -> dict[str, int]:
    if transformed is None:
        return {}
    if original.tab_ids != transformed.tab_ids or len(original.blocks) != len(transformed.blocks):
        raise ValueError
    shifts: dict[DocsTabId, int] = {}
    result: dict[str, int] = {}
    for before, after in zip(original.blocks, transformed.blocks, strict=True):
        if (before.id, before.source_range) != (
            after.id,
            after.source_range,
        ):
            raise ValueError
        if (before.kind, before.editable) != (after.kind, after.editable) and not _is_deleted_prose(
            before, after
        ):
            raise ValueError
        source = before.source_range
        if source is not None:
            result[before.id] = shifts.get(source.tab_id, 0)
        if before.text == after.text:
            continue
        if (
            not before.editable
            or source is None
            or source.segment_id is not None
            or before.native_content
            or after.native_content
        ):
            raise ValueError
        if (
            "\n" in before.text
            or "\n" in after.text
            or source.end_index - source.start_index != utf16_length(before.text)
        ):
            raise ValueError
        shifts[source.tab_id] = (
            shifts.get(source.tab_id, 0) + utf16_length(after.text) - utf16_length(before.text)
        )
    return result


def _is_deleted_prose(before: TextBlock, after: TextBlock) -> bool:
    return (
        before.kind is BlockKind.PARAGRAPH
        and before.editable
        and before.protected_suffix is None
        and (after.kind, after.text, after.editable) == (BlockKind.BLANK, "", False)
    )


def native_font_target_interval_count(document: dict[str, Any], block: TextBlock) -> int | None:
    source = block.source_range
    if source is None or block.editable or not block.font_style_ranges:
        return None
    try:
        matches = [
            element
            for element in _bodies(document)[source.tab_id]
            if element.get("startIndex") == source.start_index
            and element.get("endIndex") == source.end_index + 1
        ]
        if len(matches) != 1:
            return None
        native_paragraph_font_sha256(matches[0])
        evidence = capture_font_semantics(
            document, source.tab_id, matches[0], target_font_family="Nunito", preserve_family=False
        )
        return len(evidence.intervals)
    except _COMPONENT_ERRORS:
        return None


def verify_native_font_proof(
    document: dict[str, Any] | None,
    proofs: tuple[NativeFontProof, ...],
    *,
    preserved_block_ids: tuple[str, ...] = (),
    diagnostic_stage: Literal["resume", "headers", "content"] | None = None,
    font_policy: NativeFontVerificationPolicy | None = None,
) -> bool:
    checks: dict[str, str | int | bool] = {
        "font_proof_count": min(len(proofs), MAX_DOCUMENT_BLOCKS),
        "font_proof_verified_count": 0,
        "font_proof_reason": "document_missing",
    }
    valid = _verify_native_font_proof(
        document,
        proofs,
        preserved_block_ids,
        checks,
        preserved_family_block_ids=font_policy.preserved_family_block_ids if font_policy else (),
        require_target_family=font_policy.require_target_family if font_policy else False,
    )
    if isinstance(diagnostic_stage, str) and diagnostic_stage in {"resume", "headers", "content"}:
        if not valid:
            checks.update(_component_checks(document, proofs, checks))
        logger.info(
            "document native font proof checks={checks}",
            checks={**checks, "font_proof_stage": diagnostic_stage, "font_proof_valid": valid},
        )
    return valid


def _component_checks(
    document: dict[str, Any] | None,
    proofs: tuple[NativeFontProof, ...],
    checks: dict[str, str | int | bool],
) -> dict[str, bool]:
    ordinal = checks["font_proof_verified_count"]
    if (
        document is None
        or checks["font_proof_reason"] not in {"paragraph_payload", "parent_styles"}
        or type(ordinal) is not int
        or not 0 <= ordinal < min(len(proofs), MAX_DOCUMENT_BLOCKS)
    ):
        return {}
    proof = proofs[ordinal]
    if proof.components is None:
        return {}
    try:
        observed = _optional_components(_bodies(document)[proof.tab_id][proof.body_ordinal])
    except _COMPONENT_ERRORS:
        return {"font_proof_components_evaluated": False}
    if observed is None:
        return {"font_proof_components_evaluated": False}
    result = {
        "font_proof_components_evaluated": True,
        "font_proof_paragraph_metadata_equal": observed.paragraph_metadata_sha256
        == proof.components.paragraph_metadata_sha256,
        "font_proof_visible_residual_equal": observed.visible_residual_sha256
        == proof.components.visible_residual_sha256,
        "font_proof_terminal_residual_equal": observed.terminal_residual_sha256
        == proof.components.terminal_residual_sha256,
        "font_proof_terminal_family_representation_equal": observed.terminal_family_representation_sha256
        == proof.components.terminal_family_representation_sha256,
    }
    with suppress(*_COMPONENT_ERRORS):
        result["font_proof_parent_context_equal"] = (
            _named_styles_digest(document, proof.tab_id) == proof.named_styles_sha256
        )
    return result


def _optional_components(element: dict[str, Any]) -> NativeFontComponents | None:
    try:
        # Diagnostic-only work is bounded and must never change acceptance.
        if len(json.dumps(element).encode()) > _MAX_COMPONENT_BYTES:
            return None
        return _paragraph_components(element)
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
        RecursionError,
    ):
        return None


def _paragraph_components(element: dict[str, Any]) -> NativeFontComponents:
    payload = deepcopy(element)
    payload.pop("startIndex")
    payload.pop("endIndex")
    parts = payload["paragraph"].pop("elements")
    visible: list[dict[str, Any]] = []
    terminal: dict[str, Any] = {}
    family_representation: dict[str, Any] = {}
    for ordinal, part in enumerate(parts):
        part.pop("startIndex")
        part.pop("endIndex")
        run = part["textRun"]
        content = run.pop("content")
        if ordinal == len(parts) - 1:
            if not content.endswith("\n"):
                raise ValueError
            content = content[:-1]
            terminal = deepcopy(part)
            terminal_style = terminal["textRun"].get("textStyle", {})
            terminal_family = terminal_style.get("weightedFontFamily", {})
            family_representation = {
                "text_style_present": "textStyle" in terminal["textRun"],
                "weighted_family_present": "weightedFontFamily" in terminal_style,
                "family_present": "fontFamily" in terminal_family,
                "family": terminal_family.get("fontFamily"),
            }
            _remove_component_family(terminal)
        _remove_component_family(part)
        _append_run(visible, content, part)
    if not terminal:
        raise ValueError
    return NativeFontComponents(
        paragraph_metadata_sha256=_component_digest(payload),
        visible_residual_sha256=_component_digest(visible),
        terminal_residual_sha256=_component_digest(terminal),
        terminal_family_representation_sha256=_component_digest(family_representation),
    )


def _remove_component_family(part: dict[str, Any]) -> None:
    run = part["textRun"]
    style = run.get("textStyle", {})
    family = style.get("weightedFontFamily", {})
    family.pop("fontFamily", None)
    if not family:
        style.pop("weightedFontFamily", None)
    if not style:
        run.pop("textStyle", None)


def _component_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _verify_native_font_proof(
    document: dict[str, Any] | None,
    proofs: tuple[NativeFontProof, ...],
    preserved_block_ids: tuple[str, ...],
    checks: dict[str, str | int | bool],
    *,
    preserved_family_block_ids: tuple[str, ...],
    require_target_family: bool,
) -> bool:
    if document is None:
        return False
    try:
        return _native_font_proof_matches(
            document,
            proofs,
            preserved_block_ids,
            checks,
            preserved_family_block_ids=preserved_family_block_ids,
            require_target_family=require_target_family,
        )
    except KeyError, IndexError, TypeError, ValueError, AttributeError, UnsupportedSourceError:
        checks["font_proof_reason"] = "malformed"
        return False


def _native_font_proof_matches(
    document: dict[str, Any],
    proofs: tuple[NativeFontProof, ...],
    preserved_block_ids: tuple[str, ...],
    checks: dict[str, str | int | bool],
    *,
    preserved_family_block_ids: tuple[str, ...],
    require_target_family: bool,
) -> bool:
    bodies = _bodies(document)
    identities = {(proof.tab_id, proof.body_ordinal) for proof in proofs}
    if not _valid_font_bindings(proofs, preserved_family_block_ids):
        return False
    if not _coverage_is_complete(
        document, bodies, identities, _unbound_coverage_ids(proofs, preserved_block_ids)
    ):
        checks["font_proof_reason"] = "coverage"
        return False
    if len(identities) != len(proofs):
        checks["font_proof_reason"] = "duplicate_binding"
        return False
    for ordinal, proof in enumerate(proofs):
        element = bodies[proof.tab_id][proof.body_ordinal]
        digest = native_paragraph_font_sha256(element)
        match proof:
            case SemanticNativeFontProof():
                digest = _verified_semantic_digest(
                    document,
                    element,
                    proof,
                    preserved_family_block_ids=preserved_family_block_ids,
                    require_target_family=require_target_family,
                )
            case LegacyNativeFontProof():
                pass
            case _:
                assert_never(proof)
        if digest != proof.sha256:
            checks["font_proof_reason"] = "paragraph_payload"
            return False
        if _named_styles_digest(document, proof.tab_id) != proof.named_styles_sha256:
            checks["font_proof_reason"] = "parent_styles"
            return False
        checks["font_proof_verified_count"] = min(ordinal + 1, MAX_DOCUMENT_BLOCKS)
    checks["font_proof_reason"] = "matched"
    return True


def _valid_font_bindings(
    proofs: tuple[NativeFontProof, ...], preserved_family_ids: tuple[str, ...]
) -> bool:
    if len(proofs) > MAX_DOCUMENT_BLOCKS:
        return False
    interval_count = 0
    semantic_ids: set[str] = set()
    for proof in proofs:
        match proof:
            case LegacyNativeFontProof():
                continue
            case SemanticNativeFontProof():
                interval_count += len(proof.semantics.intervals)
                if proof.block_id in semantic_ids:
                    return False
                semantic_ids.add(proof.block_id)
            case _:
                assert_never(proof)
    return (
        interval_count <= MAX_NATIVE_FONT_INTERVALS
        and len(preserved_family_ids) == len(set(preserved_family_ids))
        and set(preserved_family_ids) <= semantic_ids
    )


def _unbound_coverage_ids(
    proofs: tuple[NativeFontProof, ...], block_ids: tuple[str, ...]
) -> tuple[str, ...]:
    semantic_ids = {
        proof.block_id for proof in proofs if isinstance(proof, SemanticNativeFontProof)
    }
    return tuple(block_id for block_id in block_ids if block_id not in semantic_ids)


def _verified_semantic_digest(
    document: dict[str, JsonValue],
    element: dict[str, JsonValue],
    proof: SemanticNativeFontProof,
    *,
    preserved_family_block_ids: tuple[str, ...],
    require_target_family: bool,
) -> str:
    semantics = proof.semantics
    if semantics.preserve_family != (proof.block_id in preserved_family_block_ids):
        raise ValueError
    # Source IR IDs can shift when an approved deletion becomes an empty
    # provider paragraph. The body ordinal and attested UTF-16 range bind it.
    return semantic_font_digest(
        document, proof.tab_id, element, semantics, require_target_family=require_target_family
    )


def _coverage_is_complete(
    document: dict[str, Any],
    bodies: dict[str, list[dict[str, Any]]],
    identities: set[tuple[DocsTabId, int]],
    block_ids: tuple[str, ...],
) -> bool:
    blocks = {block.id: block for block in parse_google_document(document, title="").blocks}
    for block_id in block_ids:
        source = blocks[block_id].source_range
        if source is None:
            return False
        matches = [
            (ordinal, element)
            for ordinal, element in enumerate(bodies[source.tab_id])
            if element.get("startIndex") == source.start_index
            and element.get("endIndex") == source.end_index + 1
        ]
        if len(matches) != 1:
            return False
        ordinal, element = matches[0]
        parts = element["paragraph"]["elements"]
        plain = all(
            isinstance(part, dict) and isinstance(part.get("textRun"), dict) for part in parts
        )
        if plain and (source.tab_id, ordinal) not in identities:
            return False
    return True


def verify_native_unstyled_text(
    source: dict[str, Any], actual: dict[str, Any], original: DocumentIR
) -> bool:
    # COPY-only exact style proof where no font override was authorized.
    try:
        return _verify_native_unstyled_text(source, actual, original)
    except KeyError, IndexError, TypeError, ValueError, AttributeError:
        return False


def _verify_native_unstyled_text(
    source: dict[str, Any], actual: dict[str, Any], original: DocumentIR
) -> bool:
    before_bodies, after_bodies = _bodies(source), _bodies(actual)
    for block in original.blocks:
        if (
            block.editable
            or block.font_style_ranges
            or not block.text
            or block.source_range is None
        ):
            continue
        location = block.source_range
        matches = [
            (ordinal, element)
            for ordinal, element in enumerate(before_bodies[location.tab_id])
            if element.get("startIndex") == location.start_index
            and element.get("endIndex") == location.end_index + 1
        ]
        if len(matches) != 1:
            return False
        ordinal, element = matches[0]
        parts = element.get("paragraph", {}).get("elements", [])
        if not all(
            isinstance(part, dict) and isinstance(part.get("textRun"), dict) for part in parts
        ):
            # Existing protected inventory binds the entire nonplain payload.
            continue
        if native_paragraph_font_sha256(
            element, preserve_font_family=True
        ) != native_paragraph_font_sha256(
            after_bodies[location.tab_id][ordinal], preserve_font_family=True
        ) or _named_styles_digest(source, location.tab_id) != _named_styles_digest(
            actual, location.tab_id
        ):
            return False
    return True
