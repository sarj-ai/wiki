from copy import deepcopy
from dataclasses import replace
import json
from typing import Any

from loguru import logger
import pytest

from app.document_transform.ir import parse_google_document
from app.document_transform.native_pagination import plan_native_pagination
from tests.test_native_spacer_layout import _spaced_document
from tests.test_transform_diagnostics_workflow import MODULE, TASK, _record


@pytest.mark.parametrize("scope", ["document", "tab"])
@pytest.mark.parametrize("name", ["NORMAL_TEXT", "HEADING_1", "HEADING_6"])
@pytest.mark.parametrize("field", ["indentStart", "shading", "borderBottom", "PRIVATE"])
def test_only_actual_style_dependencies_can_block_spacers(
    scope: str, name: str, field: str
) -> None:
    native = _spaced_document()
    target = native if scope == "document" else native["tabs"][0]["documentTab"]
    target["namedStyles"] = {"styles": [{"namedStyleType": name, "paragraphStyle": {field: {}}}]}
    before = deepcopy(native)
    original = parse_google_document(native, title="PRIVATE TITLE")
    records: list[dict[str, Any]] = []
    sink = logger.add(lambda message: records.append(message.record["extra"]))
    try:
        rules = plan_native_pagination(native, original, emit_diagnostics=True)
    finally:
        logger.remove(sink)
    accepted = name == "HEADING_6"
    assert any(rule.above_spacer_sha256 is not None for rule in rules) is accepted
    diagnostics = records[-1]["diagnostics"]
    assert diagnostics["spacer_named_styles_known"] is False
    assert diagnostics["spacer_used_named_styles_known"] is accepted
    record = _record()
    record["jsonPayload"]["record"]["message"] = "document spacer eligibility"
    record["jsonPayload"]["record"]["extra"]["diagnostics"] = diagnostics
    row = MODULE["sanitize"]([record], TASK)[0]
    assert row["spacer_used_named_styles_known"] is accepted
    assert "PRIVATE" not in json.dumps(row)
    assert native == before


@pytest.mark.parametrize(
    "mutation", ["missing", "unknown", "duplicate", "malformed", "break", "link", "dimension"]
)
def test_dependency_ambiguity_never_broadens_spacer_eligibility(mutation: str) -> None:
    native = _spaced_document()
    entry: dict[str, Any] = {"namedStyleType": "NORMAL_TEXT", "paragraphStyle": {}}
    entries = [entry]
    match mutation:
        case "missing":
            del entry["namedStyleType"]
        case "unknown":
            entry["namedStyleType"] = "PRIVATE"
        case "duplicate":
            entry["namedStyleType"] = "HEADING_6"
            entries.append(deepcopy(entry))
        case "malformed":
            entry["namedStyleType"] = []
        case "break":
            entry["namedStyleType"] = "HEADING_6"
            entry["paragraphStyle"] = {"pageBreakBefore": True}
        case "link":
            entry["textStyle"] = {"link": {"url": "https://example.com"}}
        case _:
            entry["paragraphStyle"] = {"spaceAbove": {"magnitude": True, "unit": "PT"}}
    native["namedStyles"] = {"styles": entries}
    original = parse_google_document(native, title="Layout")
    rules = plan_native_pagination(native, original)
    assert all(
        rule.above_spacer_sha256 is None and rule.below_spacer_sha256 is None for rule in rules
    )


def test_destination_heading_dependency_is_checked_even_with_normal_source_provenance() -> None:
    native = _spaced_document()
    native["namedStyles"] = {
        "styles": [{"namedStyleType": "HEADING_1", "paragraphStyle": {"shading": {}}}]
    }
    original = parse_google_document(native, title="Layout")
    blocks = tuple(replace(block, source_named_style="NORMAL_TEXT") for block in original.blocks)
    rules = plan_native_pagination(native, original)
    changed = plan_native_pagination(native, replace(original, blocks=blocks))
    assert changed == rules
    assert all(rule.above_spacer_sha256 is None for rule in changed)


@pytest.mark.parametrize("source_style", ["HEADING_6", "PRIVATE", None])
def test_source_only_or_unknown_dependency_is_not_ignored(source_style: str | None) -> None:
    native = _spaced_document()
    native["namedStyles"] = {
        "styles": [
            {
                "namedStyleType": "HEADING_6",
                "paragraphStyle": {"indentStart": {"magnitude": 0, "unit": "PT"}},
            }
        ]
    }
    original = parse_google_document(native, title="Layout")
    assert any(
        rule.above_spacer_sha256 is not None for rule in plan_native_pagination(native, original)
    )
    changed = replace(
        original,
        blocks=(replace(original.blocks[0], source_named_style=source_style), *original.blocks[1:]),
    )
    rules = plan_native_pagination(native, changed)
    assert all(rule.above_spacer_sha256 is None for rule in rules)
