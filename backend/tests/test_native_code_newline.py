from copy import deepcopy
from dataclasses import dataclass
import json

from pydantic import JsonValue
import pytest

from app.document_transform.ir import parse_google_document
from app.document_transform.native_code import (
    NativeCodeStylePlan,
    plan_native_code_styles,
    verify_native_code_styles,
)
from app.document_transform.native_code_source import (
    NativeCodeRecord,
    code_array,
    code_bodies,
    code_index,
    code_object,
    code_string,
)
from app.identifiers import DocsTabId
from tests.test_native_code import _body, _document, _parts


@dataclass(frozen=True, slots=True)
class _ObservedCode:
    plan: NativeCodeStylePlan
    actual: NativeCodeRecord


@pytest.fixture
def comment_source() -> NativeCodeRecord:
    return _document(
        [
            ("```python\n", "Courier New"),
            ("count = len(operations)  # Preserve 😀 exactly.\n", "Courier New"),
            ("```\n", "Courier New"),
        ]
    )


def _observed(source: NativeCodeRecord, *, coalesced: bool = False) -> _ObservedCode:
    parsed = parse_google_document(source, title="Native code readback")
    plan = plan_native_code_styles(source, parsed, parsed)
    actual = deepcopy(source)
    for item in plan.paragraphs:
        code_bodies(actual)[DocsTabId("t.0")][item.body_ordinal] = json.loads(item.expected_json)
    paragraph = code_object(_body(actual)[1]["paragraph"])
    parts = _parts(paragraph)
    tail = parts[-1]
    previous = parts[-2]
    tail_run = code_object(tail["textRun"])
    previous_run = code_object(previous["textRun"])
    assert tail_run["content"] == "\n"
    assert previous["endIndex"] == tail["startIndex"]
    code_object(tail_run.setdefault("textStyle", {}))["foregroundColor"] = deepcopy(
        code_object(previous_run["textStyle"])["foregroundColor"]
    )
    if coalesced:
        assert previous_run["textStyle"] == tail_run["textStyle"]
        previous_run["content"] = f"{code_string(previous_run['content'])}\n"
        previous["endIndex"] = tail["endIndex"]
        parts.pop()
    paragraph["elements"] = list(parts)
    return _ObservedCode(plan, actual)


@pytest.mark.parametrize("coalesced", [False, True])
def test_real_provider_comment_color_extension_preserves_the_complete_plan(
    comment_source: NativeCodeRecord, coalesced: bool
) -> None:
    observed = _observed(comment_source, coalesced=coalesced)
    source_before, actual_before = deepcopy(comment_source), deepcopy(observed.actual)
    requests, plan_hash = deepcopy(observed.plan.requests), observed.plan.sha256

    assert verify_native_code_styles(comment_source, observed.actual, observed.plan)
    assert comment_source == source_before
    assert observed.actual == actual_before
    assert observed.plan.requests == requests
    assert observed.plan.sha256 == plan_hash


@pytest.mark.parametrize("container", ["absent", "empty", "explicit-color"])
def test_newline_extension_restores_only_source_foreground_representation(
    comment_source: NativeCodeRecord, container: str
) -> None:
    element = _body(comment_source)[1]
    paragraph = code_object(element["paragraph"])
    parts = code_array(paragraph["elements"])
    part = code_object(parts[0])
    run = code_object(part["textRun"])
    newline = deepcopy(part)
    newline["startIndex"] = code_index(part["endIndex"]) - 1
    newline_run = code_object(newline["textRun"])
    newline_run["content"] = "\n"
    part["endIndex"] = code_index(part["endIndex"]) - 1
    run["content"] = code_string(run["content"]).removesuffix("\n")
    newline_run.pop("textStyle")
    if container != "absent":
        newline_run["textStyle"] = (
            {"foregroundColor": {"color": {"rgbColor": {"red": 0.5}}}}
            if container == "explicit-color"
            else {}
        )
    parts.append(newline)
    observed = _observed(comment_source)

    assert verify_native_code_styles(comment_source, observed.actual, observed.plan)


@pytest.mark.parametrize(
    "tamper", ["color", "bold", "font", "link", "background", "literal", "index", "object"]
)
def test_a_code_newline_exception_does_not_hide_other_native_damage(
    comment_source: NativeCodeRecord, tamper: str
):
    observed = _observed(comment_source)
    paragraph = code_object(_body(observed.actual)[1]["paragraph"])
    parts = _parts(paragraph)
    tail = parts[-1]
    style = code_object(code_object(tail["textRun"])["textStyle"])
    changes: dict[str, tuple[str, JsonValue]] = {
        "color": ("foregroundColor", {"color": {"rgbColor": {"red": 1}}}),
        "bold": ("bold", True),
        "font": ("weightedFontFamily", {"fontFamily": "Arial"}),
        "link": ("link", {"url": "https://example.org/unplanned"}),
        "background": ("backgroundColor", {"color": {"rgbColor": {"red": 1}}}),
    }
    if tamper in changes:
        key, value = changes[tamper]
        style[key] = value
    elif tamper == "literal":
        run = code_object(parts[-2]["textRun"])
        run["content"] = code_string(run["content"]).replace("Preserve", "preserve")
    elif tamper == "index":
        tail["startIndex"] = code_index(tail["startIndex"]) - 1
    else:
        tail["inlineObjectElement"] = {"inlineObjectId": "unplanned-object"}

    assert not verify_native_code_styles(comment_source, observed.actual, observed.plan)


def test_code_color_cannot_extend_across_uncolored_visible_text():
    source = _document(
        [
            ("```python\n", "Courier New"),
            ('print("µs 😀")\n', "Courier New"),
            ("```\n", "Courier New"),
        ]
    )
    parsed = parse_google_document(source, title="Nonadjacent token")
    plan = plan_native_code_styles(source, parsed, parsed)
    actual = deepcopy(source)
    for item in plan.paragraphs:
        code_bodies(actual)[DocsTabId("t.0")][item.body_ordinal] = json.loads(item.expected_json)
    tail = _parts(code_object(_body(actual)[1]["paragraph"]))[-1]
    code_object(code_object(tail["textRun"]).setdefault("textStyle", {}))["foregroundColor"] = {
        "color": {"rgbColor": {"green": 0.42745098, "blue": 0.46666667}}
    }

    assert not verify_native_code_styles(source, actual, plan)
