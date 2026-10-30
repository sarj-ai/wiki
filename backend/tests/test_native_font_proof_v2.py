from copy import deepcopy
from dataclasses import replace
from typing import Any, Literal

from pydantic import ValidationError
import pytest

from app.document_transform.ir import parse_google_document, utf16_length
from app.document_transform.native_font_context import resolve_native_font_values
from app.document_transform.native_font_proof import (
    NativeFontProof,
    NativeFontVerificationPolicy,
    capture_native_font_proof,
    native_font_target_interval_count,
    verify_native_font_proof,
)
from tests.test_native_preservation_findings import _native


def _source() -> dict[str, Any]:
    document = _native()
    document["tabs"][0]["documentTab"]["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "NORMAL_TEXT",
                "textStyle": {
                    "bold": False,
                    "weightedFontFamily": {"fontFamily": "Arial", "weight": 400},
                },
            }
        ]
    }
    return document


def _parts(document: dict[str, Any]) -> list[dict[str, Any]]:
    return document["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["elements"]


def _proofs(document: dict[str, Any], *, preserved: bool = False) -> tuple[NativeFontProof, ...]:
    return capture_native_font_proof(
        document,
        parse_google_document(document, title=""),
        target_font_family="Nunito",
        preserved_family_block_ids=("b0",) if preserved else (),
    )


def _target(document: dict[str, Any]) -> dict[str, Any]:
    actual = deepcopy(document)
    for part in _parts(actual):
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito", "weight": 400}
    return actual


def test_v2_explicit_terminal_family_and_materialized_weight_preserve_emphasis():
    source = _source()
    proofs = _proofs(source)
    assert proofs[0].version == 2
    assert verify_native_font_proof(source, proofs)
    assert not verify_native_font_proof(
        source, proofs, font_policy=NativeFontVerificationPolicy(require_target_family=True)
    )
    actual = _target(source)
    assert verify_native_font_proof(
        actual, proofs, font_policy=NativeFontVerificationPolicy(require_target_family=True)
    )
    restored = tuple(
        NativeFontProof.model_validate_json(proof.model_dump_json()) for proof in proofs
    )
    assert verify_native_font_proof(
        actual, restored, font_policy=NativeFontVerificationPolicy(require_target_family=True)
    )


@pytest.mark.parametrize("terminal_family", ["Arial", None], ids=["source-family", "inherited"])
def test_v2_accepts_provider_canonicalized_terminal_only_family(
    terminal_family: str | None,
):
    source = _source()
    actual = _target(source)
    parts = _parts(actual)
    visible = parts[-1]
    terminal = deepcopy(visible)
    visible["endIndex"] -= 1
    visible["textRun"]["content"] = "Beta"
    terminal["startIndex"] = terminal["endIndex"] - 1
    terminal["textRun"]["content"] = "\n"
    family = terminal["textRun"]["textStyle"]["weightedFontFamily"]
    if terminal_family is None:
        family.pop("fontFamily")
    else:
        family["fontFamily"] = terminal_family
    parts.append(terminal)

    assert verify_native_font_proof(
        actual,
        _proofs(source),
        font_policy=NativeFontVerificationPolicy(require_target_family=True),
    )
    terminal["textRun"]["textStyle"]["bold"] = True
    assert not verify_native_font_proof(
        actual,
        _proofs(source),
        font_policy=NativeFontVerificationPolicy(require_target_family=True),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bold", False),
        ("bold", None),
        ("italic", True),
        ("underline", True),
        ("foregroundColor", {"color": {"rgbColor": {"red": 1}}}),
        ("link", {"url": "https://example.test/private"}),
        ("unknownStyle", "private"),
        ("weightedFontFamily", {"fontFamily": "Nunito", "weight": 700}),
        ("weightedFontFamily", {"fontFamily": "Nunito", "weight": False}),
        ("weightedFontFamily", {"fontFamily": "Other", "weight": 400}),
    ],
)
def test_v2_rejects_real_style_loss_and_unknown_changes(field: str, value: object):
    source = _source()
    actual = _target(source)
    _parts(actual)[0]["textRun"]["textStyle"][field] = value
    assert not verify_native_font_proof(
        actual,
        _proofs(source),
        font_policy=NativeFontVerificationPolicy(require_target_family=True),
    )


def test_equivalent_rendered_weight_does_not_authorize_bold_weight_tradeoff():
    source = _source()
    actual = _target(source)
    _parts(actual)[0]["textRun"]["textStyle"].update(
        {"bold": False, "weightedFontFamily": {"fontFamily": "Nunito", "weight": 700}}
    )
    assert not verify_native_font_proof(
        actual,
        _proofs(source),
        font_policy=NativeFontVerificationPolicy(require_target_family=True),
    )


def test_unknown_parents_preserve_entire_block_exactly():
    source = _native()
    with pytest.raises(ValueError, match=r"^$"):
        _proofs(source)
    proofs = _proofs(source, preserved=True)
    assert verify_native_font_proof(
        source,
        proofs,
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=("b0",), require_target_family=True
        ),
    )
    assert not verify_native_font_proof(source, proofs)
    assert not verify_native_font_proof(
        _target(source),
        proofs,
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=("b0",), require_target_family=True
        ),
    )


@pytest.mark.parametrize(
    "change", ["parent", "indices", "wrapper", "text", "terminal_bold", "terminal_family"]
)
def test_v2_rejects_unauthorized_topology_and_terminal_changes(
    change: Literal["parent", "indices", "wrapper", "text", "terminal_bold", "terminal_family"],
):
    source = _source()
    actual = _target(source)
    match change:
        case "parent":
            actual["tabs"][0]["documentTab"]["namedStyles"]["styles"][0]["textStyle"]["bold"] = True
        case "indices":
            _parts(actual)[0]["startIndex"] = 2
        case "wrapper":
            _parts(actual)[0]["private"] = "private"
        case "text":
            _parts(actual)[0]["textRun"]["content"] = "Other "
        case "terminal_bold":
            _parts(actual)[-1]["textRun"]["textStyle"]["bold"] = True
        case "terminal_family":
            _parts(actual)[-1]["textRun"]["textStyle"]["weightedFontFamily"]["fontFamily"] = "Arial"
    assert not verify_native_font_proof(
        actual,
        _proofs(source),
        font_policy=NativeFontVerificationPolicy(require_target_family=True),
    )


def test_legacy_proof_remains_v1_and_rejects_terminal_family_change():
    source = _source()
    proofs = capture_native_font_proof(source, parse_google_document(source, title=""))
    assert proofs[0].version == 1
    assert not verify_native_font_proof(_target(source), proofs)
    raw = proofs[0].model_dump()
    raw.pop("semantics")
    raw.pop("block_id")
    assert verify_native_font_proof(source, (NativeFontProof.model_validate(raw),))
    with pytest.raises(ValidationError):
        NativeFontProof.model_validate({**raw, "version": 2})


@pytest.mark.parametrize(
    "field", ["source_family_sha256", "target_font_family", "preserve_family", "intervals"]
)
def test_corrupt_semantic_evidence_cannot_be_ignored(field: str):
    source = _source()
    proof = _proofs(source)[0]
    assert proof.semantics is not None
    value: object = {
        "source_family_sha256": "0" * 64,
        "target_font_family": "Arial",
        "preserve_family": True,
        "intervals": (),
    }[field]
    altered = proof.model_copy(
        update={"semantics": proof.semantics.model_copy(update={field: value})}
    )
    assert not verify_native_font_proof(
        _target(source),
        (altered,),
        font_policy=NativeFontVerificationPolicy(require_target_family=True),
    )


@pytest.mark.parametrize("invalid", [None, False, 10**1000, 401, "400"])
def test_malformed_explicit_weight_never_falls_back(invalid: object):
    assert (
        resolve_native_font_values(
            _source(), "tab", {}, {"bold": True, "weightedFontFamily": {"weight": invalid}}
        ).weight
        is None
    )


@pytest.mark.parametrize("content", ["Alpha ", "😀abcd", "éabcd", "👩‍💻 "])
def test_utf16_fragmentation_preserves_exact_style_intervals(content: str):
    source = _source()
    _parts(source)[0]["textRun"]["content"] = content
    actual = _target(source)
    first = _parts(actual)[0]
    second = deepcopy(first)
    width = len(content[0].encode("utf-16-le")) // 2
    first["endIndex"] = first["startIndex"] + width
    first["textRun"]["content"] = content[0]
    second["startIndex"] = first["endIndex"]
    second["textRun"]["content"] = content[1:]
    _parts(actual).insert(1, second)
    assert verify_native_font_proof(
        actual,
        _proofs(source),
        font_policy=NativeFontVerificationPolicy(require_target_family=True),
    )


def test_target_budget_declines_while_exact_preserve_remains_available():
    source = _source()
    block = parse_google_document(source, title="").blocks[0]
    assert native_font_target_interval_count(source, block) == 2
    source["tabs"][0]["documentTab"]["body"]["content"][0]["paragraph"]["privateMetadata"] = (
        "SECRET" * 30000
    )
    assert native_font_target_interval_count(source, block) is None
    proofs = _proofs(source, preserved=True)
    assert verify_native_font_proof(
        source,
        proofs,
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=("b0",), require_target_family=True
        ),
    )
    assert "SECRET" not in proofs[0].model_dump_json()


@pytest.mark.parametrize("changed", [False, True])
def test_unknown_font_object_fields_remain_exact(*, changed: bool):
    source = _source()
    _parts(source)[0]["textRun"]["textStyle"]["weightedFontFamily"] = {
        "fontFamily": "Arial",
        "weight": 400,
        "unknown": "PRIVATE",
    }
    actual = deepcopy(source)
    _parts(actual)[0]["textRun"]["textStyle"]["weightedFontFamily"]["unknown"] = (
        "OTHER" if changed else "PRIVATE"
    )
    assert verify_native_font_proof(
        actual,
        _proofs(source, preserved=True),
        font_policy=NativeFontVerificationPolicy(
            preserved_family_block_ids=("b0",), require_target_family=True
        ),
    ) is (not changed)


def test_duplicate_parent_identity_is_unproven_even_with_explicit_values():
    source = _source()
    styles = source["tabs"][0]["documentTab"]["namedStyles"]["styles"]
    styles.append(deepcopy(styles[0]))
    assert (
        resolve_native_font_values(
            source, "tab", {}, {"bold": True, "weightedFontFamily": {"weight": 400}}
        ).bold
        is None
    )


@pytest.mark.parametrize("family", ["Arial", "Nunito"])
def test_explicit_family_without_weight_never_inherits_parent_weight(family: str):
    source = _source()
    source["tabs"][0]["documentTab"]["namedStyles"]["styles"][0]["textStyle"]["weightedFontFamily"][
        "weight"
    ] = 700
    assert (
        resolve_native_font_values(
            source, "tab", {}, {"weightedFontFamily": {"fontFamily": family}}
        ).weight
        is None
    )


def test_inherited_unknown_family_fields_decline_target_scope():
    source = _source()
    source["tabs"][0]["documentTab"]["namedStyles"]["styles"][0]["textStyle"]["weightedFontFamily"][
        "unknown"
    ] = "private"
    assert resolve_native_font_values(source, "tab", {}, {}).weight is None
    assert (
        native_font_target_interval_count(source, parse_google_document(source, title="").blocks[0])
        is None
    )


def _shift(element: dict[str, Any], delta: int) -> None:
    element["startIndex"] += delta
    element["endIndex"] += delta
    for part in element["paragraph"]["elements"]:
        part["startIndex"] += delta
        part["endIndex"] += delta


@pytest.mark.parametrize("prefix", ["Moreover, Lead.", "😀 Lead."])
def test_preserved_paragraph_accepts_only_exact_planned_utf16_shift(prefix: str):
    source = _source()
    body = source["tabs"][0]["documentTab"]["body"]["content"]
    width = utf16_length(prefix + "\n")
    _shift(body[0], width)
    body.insert(
        0,
        {
            "startIndex": 1,
            "endIndex": 1 + width,
            "paragraph": {
                "elements": [
                    {
                        "startIndex": 1,
                        "endIndex": 1 + width,
                        "textRun": {"content": prefix + "\n", "textStyle": {}},
                    }
                ]
            },
        },
    )
    original = parse_google_document(source, title="")
    transformed = replace(
        original, blocks=(replace(original.blocks[0], text="Lead."), original.blocks[1])
    )
    proofs = capture_native_font_proof(
        source,
        original,
        transformed=transformed,
        target_font_family="Nunito",
        preserved_family_block_ids=("b1",),
    )
    before = NativeFontVerificationPolicy(preserved_family_block_ids=("b1",))
    after = NativeFontVerificationPolicy(
        preserved_family_block_ids=("b1",), require_target_family=True
    )
    assert verify_native_font_proof(source, proofs, font_policy=before)
    assert not verify_native_font_proof(source, proofs, font_policy=after)
    actual = deepcopy(source)
    actual_body = actual["tabs"][0]["documentTab"]["body"]["content"]
    delta = utf16_length("Lead.") - utf16_length(prefix)
    actual_body[0]["endIndex"] += delta
    actual_body[0]["paragraph"]["elements"][0]["endIndex"] += delta
    actual_body[0]["paragraph"]["elements"][0]["textRun"]["content"] = "Lead.\n"
    _shift(actual_body[1], delta)
    restored = tuple(
        NativeFontProof.model_validate_json(proof.model_dump_json()) for proof in proofs
    )
    assert verify_native_font_proof(actual, restored, font_policy=after)
    _shift(actual_body[1], 1)
    assert not verify_native_font_proof(actual, restored, font_policy=before)
    assert not verify_native_font_proof(actual, restored, font_policy=after)


def _many_intervals(count: int, paragraphs: int = 1) -> dict[str, Any]:
    source = _source()
    body = source["tabs"][0]["documentTab"]["body"]["content"]
    body.clear()
    for paragraph in range(paragraphs):
        start = paragraph * count + 1
        body.append(
            {
                "startIndex": start,
                "endIndex": start + count,
                "paragraph": {
                    "elements": [
                        {
                            "startIndex": start + index,
                            "endIndex": start + index + 1,
                            "textRun": {
                                "content": "\n" if index == count - 1 else "x",
                                "textStyle": {"bold": bool(index % 2)},
                            },
                        }
                        for index in range(count)
                    ]
                },
            }
        )
    return source


def test_interval_budgets_support_exact_preserved_fallback():
    source = _many_intervals(1001)
    block = parse_google_document(source, title="").blocks[0]
    assert native_font_target_interval_count(source, block) is None
    preserved = _proofs(source, preserved=True)
    assert verify_native_font_proof(
        source,
        preserved,
        font_policy=NativeFontVerificationPolicy(preserved_family_block_ids=("b0",)),
    )
    source = _many_intervals(1000, 6)
    original = parse_google_document(source, title="")
    assert all(
        native_font_target_interval_count(source, block) == 1000 for block in original.blocks
    )
    with pytest.raises(ValueError, match=r"^$"):
        capture_native_font_proof(source, original, target_font_family="Nunito")
    proofs = capture_native_font_proof(
        source, original, target_font_family="Nunito", preserved_family_block_ids=("b5",)
    )
    assert (
        sum(len(proof.semantics.intervals) for proof in proofs if proof.semantics is not None)
        == 5000
    )
    assert verify_native_font_proof(
        source, proofs, font_policy=NativeFontVerificationPolicy(preserved_family_block_ids=("b5",))
    )
