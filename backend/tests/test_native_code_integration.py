from copy import deepcopy
import json

import pytest

from app.document_transform.brand import SarjBrandProfile, build_copy_mutations
from app.document_transform.ir import BlockKind, parse_google_document, utf16_length
from app.document_transform.native_code import plan_native_code_styles
from app.document_transform.native_code_source import code_object
from app.document_transform.unslop import model_editable_blocks, scan_unslop_candidates
from tests.test_copy_font_preservation import _source, _verify
from tests.test_document_transform_typography import _document, _run


@pytest.mark.parametrize("font", ["Fira Code", "JetBrains Mono", "monospace"])
def test_native_code_is_protected_before_unslop(font: str) -> None:
    raw = _document(
        [
            _run(
                'def example():\n    return "Moreover, a game-changer"\n',
                weightedFontFamily={"fontFamily": font},
            )
        ]
    )
    before = deepcopy(raw)
    parsed = parse_google_document(raw, title="Code")

    assert parsed.blocks[0].kind is BlockKind.CODE
    assert not parsed.blocks[0].editable
    assert model_editable_blocks(parsed) == ()
    assert scan_unslop_candidates(parsed) == ()
    assert raw == before


def _native_source():
    raw = _source()
    text = "def add(left, right):\n    return left + right  # 🧪 Keep this.\n"
    raw["tabs"][0]["documentTab"]["body"]["content"] = [
        {
            "startIndex": 1,
            "endIndex": 1 + utf16_length(text),
            "paragraph": {
                "elements": [
                    {
                        "startIndex": 1,
                        "endIndex": 1 + utf16_length(text),
                        "textRun": {
                            "content": text,
                            "textStyle": {"weightedFontFamily": {"fontFamily": "Courier New"}},
                        },
                    }
                ],
            },
        }
    ]
    return raw


def test_copy_code_styles_are_source_bound_and_do_not_authorize_font_changes() -> None:
    raw = _native_source()
    parsed = parse_google_document(raw, title="Code")
    profile = SarjBrandProfile()
    plain = build_copy_mutations(parsed, parsed, profile=profile, headers=(), logo_uri="")
    styled = build_copy_mutations(
        parsed, parsed, source_document=raw, profile=profile, headers=(), logo_uri=""
    )
    plan = plan_native_code_styles(raw, parsed, parsed)

    assert plan.requests
    assert styled == plain + plan.requests
    assert {item.language for item in plan.paragraphs} == {"python"}
    assert all(
        code_object(item["updateTextStyle"])["fields"] == "foregroundColor"
        for item in plan.requests
        if "updateTextStyle" in item
    )


@pytest.mark.parametrize("tamper", ["none", "missing", "literal", "font", "unknown", "color"])
async def test_public_copy_verification_proves_native_syntax_and_residual_styles(
    monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    source = _native_source()
    parsed = parse_google_document(source, title="Decision")
    plan = plan_native_code_styles(source, parsed, parsed)
    actual = deepcopy(source)
    body = actual["tabs"][0]["documentTab"]["body"]["content"]
    body[0] = json.loads(plan.paragraphs[0].expected_json)
    part = body[0]["paragraph"]["elements"][0]
    if tamper == "missing":
        body[0] = deepcopy(source["tabs"][0]["documentTab"]["body"]["content"][0])
    elif tamper == "literal":
        part["textRun"]["content"] = part["textRun"]["content"].replace("def", "del")
    elif tamper == "font":
        part["textRun"]["textStyle"]["weightedFontFamily"] = {"fontFamily": "Nunito"}
    elif tamper == "unknown":
        part["textRun"]["textStyle"]["futureStyle"] = True
    elif tamper == "color":
        part["textRun"]["textStyle"]["foregroundColor"] = {"color": {"rgbColor": {}}}
    result = await _verify(monkeypatch, source, actual)

    assert result.structure_preserved is (tamper == "none")
    if tamper == "none":
        assert result.content_preserved
        assert result.brand_applied
