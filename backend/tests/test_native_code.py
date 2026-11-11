from copy import deepcopy
from dataclasses import replace

from pydantic import JsonValue
import pytest

from app.document_transform.ir import BlockKind, parse_google_document, utf16_length
from app.document_transform.native_code import (
    plan_native_code_styles,
    project_native_code_styles,
    protect_native_code,
    verify_native_code_styles,
)
from app.document_transform.native_code_source import (
    NativeCodeRecord,
    code_array,
    code_index,
    code_object,
    code_string,
)
from app.document_transform.native_font_proof import verify_native_unstyled_text
from app.document_transform.safety import validate_patches
from app.document_transform.schemas import TextPatch, TransformPhase
from app.document_transform.unslop import model_editable_blocks


def _document(lines: list[tuple[str, str]]) -> NativeCodeRecord:
    content: list[JsonValue] = []
    cursor = 1
    for text, family in lines:
        end = cursor + utf16_length(text)
        content.append(
            {
                "startIndex": cursor,
                "endIndex": end,
                "paragraph": {
                    "paragraphStyle": {
                        "namedStyleType": "NORMAL_TEXT",
                        "alignment": "START",
                        "indentStart": {"magnitude": 12, "unit": "PT"},
                        "lineSpacing": 100,
                    },
                    "elements": [
                        {
                            "startIndex": cursor,
                            "endIndex": end,
                            "textRun": {
                                "content": text,
                                "textStyle": {
                                    "weightedFontFamily": {"fontFamily": family, "weight": 400},
                                    "fontSize": {"magnitude": 10, "unit": "PT"},
                                    "bold": False,
                                },
                            },
                        }
                    ],
                },
            }
        )
        cursor = end
    return {
        "revisionId": "source-revision",
        "tabs": [
            {"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": {"content": content}}}
        ],
    }


def _body(document: NativeCodeRecord) -> list[NativeCodeRecord]:
    tab = code_object(code_array(document["tabs"])[0])
    document_tab = code_object(tab["documentTab"])
    body = code_object(document_tab["body"])
    return [code_object(value) for value in code_array(body["content"])]


def _paragraph(document: NativeCodeRecord, ordinal: int = 0) -> NativeCodeRecord:
    return code_object(_body(document)[ordinal]["paragraph"])


def _parts(paragraph: NativeCodeRecord) -> list[NativeCodeRecord]:
    return [code_object(value) for value in code_array(paragraph["elements"])]


def _run(document: NativeCodeRecord, ordinal: int = 0) -> NativeCodeRecord:
    return code_object(_parts(_paragraph(document, ordinal))[0]["textRun"])


def _apply(document: NativeCodeRecord, requests: list[NativeCodeRecord]) -> NativeCodeRecord:
    actual = deepcopy(document)
    _fragment_runs(actual)
    for request in requests:
        _apply_request(actual, request)
    return actual


def _fragment_runs(document: NativeCodeRecord) -> None:
    for element in _body(document):
        paragraph = code_object(element["paragraph"])
        characters: list[JsonValue] = []
        for part in _parts(paragraph):
            cursor = code_index(part["startIndex"])
            run = code_object(part["textRun"])
            for character in code_string(run["content"]):
                end = cursor + utf16_length(character)
                characters.append(
                    {
                        "startIndex": cursor,
                        "endIndex": end,
                        "textRun": {"content": character, "textStyle": deepcopy(run["textStyle"])},
                    }
                )
                cursor = end
        paragraph["elements"] = characters


def _apply_request(document: NativeCodeRecord, request: NativeCodeRecord) -> None:
    kind, value = next(iter(request.items()))
    update = code_object(value)
    target = code_object(update["range"])
    for element in _body(document):
        if code_index(element["startIndex"]) >= code_index(target["endIndex"]) or code_index(
            element["endIndex"]
        ) <= code_index(target["startIndex"]):
            continue
        paragraph = code_object(element["paragraph"])
        if kind == "updateParagraphStyle":
            code_object(paragraph["paragraphStyle"]).update(code_object(update["paragraphStyle"]))
        else:
            _apply_foreground(paragraph, target, code_object(update["textStyle"]))


def _apply_foreground(
    paragraph: NativeCodeRecord, target: NativeCodeRecord, style: NativeCodeRecord
) -> None:
    for part in _parts(paragraph):
        if code_index(target["startIndex"]) <= code_index(part["startIndex"]) and code_index(
            part["endIndex"]
        ) <= code_index(target["endIndex"]):
            run = code_object(part["textRun"])
            code_object(run["textStyle"]).update(deepcopy(style))


def _protected(document: NativeCodeRecord):
    return protect_native_code(parse_google_document(document, title="Code"), document)


def test_code_requests_follow_utf16_prose_edits_and_preserve_native_blank_lines():
    source = _document(
        [
            ("An unnecessarily verbose 🦊 opening paragraph.\n", "Arial"),
            ("def budget(ns):\n", "Courier New"),
            ("\treturn ns / 1_000\n", "Courier New"),
            ("\n", "Arial"),
            ("print('µs 😀')\n", "Courier New"),
            ("Ordinary closing prose.\n", "Arial"),
        ]
    )
    untouched = deepcopy(source)
    original = _protected(source)
    first = original.blocks[0]
    transformed = original.replace_blocks({first.id: "Short 🦊 opening."})
    plan = plan_native_code_styles(source, original, transformed)
    assert len(plan.paragraphs) == 4
    assert sum(item.block_id is None for item in plan.paragraphs) == 1
    assert {item.language for item in plan.paragraphs} == {"python"}
    assert set(plan.block_ids) == {
        block.id for block in original.blocks if block.kind is BlockKind.CODE
    }
    assert {
        code_string(code_object(update)["fields"])
        for request in plan.requests
        for update in request.values()
    } == {"foregroundColor", "shading.backgroundColor,keepLinesTogether"}
    actual = deepcopy(source)
    replacement = transformed.blocks[0].text + "\n"
    delta = utf16_length(replacement) - utf16_length(code_string(_run(actual)["content"]))
    _run(actual)["content"] = replacement
    code_object(_body(actual)[0])["endIndex"] = (
        code_index(code_object(_body(actual)[0])["endIndex"]) + delta
    )
    code_object(_parts(_paragraph(actual, 0))[0])["endIndex"] = (
        code_index(code_object(_parts(_paragraph(actual, 0))[0])["endIndex"]) + delta
    )
    for element in _body(actual)[1:]:
        for record in [element, *_parts(code_object(element["paragraph"]))]:
            record["startIndex"] = code_index(record["startIndex"]) + delta
            record["endIndex"] = code_index(record["endIndex"]) + delta
    actual = _apply(actual, plan.requests)
    assert verify_native_code_styles(source, actual, plan)
    projected = project_native_code_styles(source, actual, plan)
    assert verify_native_unstyled_text(source, projected, original)
    assert _body(projected)[0] == _body(actual)[0]
    assert source == untouched
    assert [
        block.text for block in _protected(projected).blocks if block.kind is BlockKind.CODE
    ] == [block.text for block in original.blocks if block.kind is BlockKind.CODE]


def test_native_soft_line_code_is_kept_together_with_exact_scoped_projection():
    source = _document(
        [
            ("Ordinary prefix.\n", "Arial"),
            ("def ready():\v\treturn 'yes'\n", "Courier New"),
            ("Ordinary suffix.\n", "Arial"),
        ]
    )
    source_style = code_object(_paragraph(source, 1)["paragraphStyle"])
    source_style.update({"keepLinesTogether": False, "keepWithNext": False})
    original = _protected(source)
    plan = plan_native_code_styles(source, original, original)
    assert len(plan.paragraphs) == 1
    actual = _apply(source, plan.requests)
    actual_style = code_object(_paragraph(actual, 1)["paragraphStyle"])
    assert actual_style["keepLinesTogether"] is True
    assert actual_style["keepWithNext"] is False
    assert verify_native_code_styles(source, actual, plan)
    projected = project_native_code_styles(source, actual, plan)
    assert _body(projected)[1] == _body(source)[1]
    assert _body(projected)[0] == _body(actual)[0]
    assert _body(projected)[2] == _body(actual)[2]
    legacy = replace(
        plan, syntax_version=plan.syntax_version.replace("native-code-v2/", "native-code-v1/", 1)
    )
    assert not verify_native_code_styles(source, actual, legacy)


@pytest.mark.parametrize("keep_together", [None, False, 1, "true"])
def test_native_code_readback_requires_actual_keep_lines_together(keep_together: JsonValue):
    source = _document([("def ready():\v\treturn 'yes'\n", "Courier New")])
    original = _protected(source)
    plan = plan_native_code_styles(source, original, original)
    actual = _apply(source, plan.requests)
    paragraph = _paragraph(actual)
    style = {
        key: value
        for key, value in code_object(paragraph["paragraphStyle"]).items()
        if key != "keepLinesTogether"
    }
    if keep_together is not None:
        style["keepLinesTogether"] = keep_together
    paragraph["paragraphStyle"] = style
    assert not verify_native_code_styles(source, actual, plan)


def test_ambiguous_mono_is_protected_plain_text_without_style_requests():
    source = _document([("Alpha and Beta are editorial labels.\n", "Courier New")])
    original = _protected(source)
    block = original.blocks[0]
    assert block.kind is BlockKind.CODE
    assert not block.editable
    assert model_editable_blocks(original) == ()
    decision = validate_patches(
        original,
        [
            TextPatch(
                block_id=block.id, expected_sha256=block.sha256, replacement="Rewritten labels."
            )
        ],
        phase=TransformPhase.UNSLOP,
    )
    assert decision.accepted == {}
    assert [finding.code for finding in decision.findings] == ["protected_block"]
    plan = plan_native_code_styles(source, original, original)
    assert plan.requests == []
    actual = deepcopy(source)
    actual["namedStyles"] = {"styles": []}
    assert project_native_code_styles(source, actual, plan) == actual
    legacy = replace(
        plan, syntax_version=plan.syntax_version.replace("native-code-v2/", "native-code-v1/", 1)
    )
    assert not verify_native_code_styles(source, actual, legacy)


def test_explicit_fenced_typescript_retains_fences_language_hint_and_authored_font():
    source = _document(
        [("```ts\n", "Arial"), ("const elapsed: number = 150;\n", "Arial"), ("```\n", "Arial")]
    )
    original = _protected(source)
    assert all(block.kind is BlockKind.CODE and (not block.editable) for block in original.blocks)
    plan = plan_native_code_styles(source, original, original)
    assert {item.language for item in plan.paragraphs} == {"typescript"}
    actual = _apply(source, plan.requests)
    assert verify_native_code_styles(source, actual, plan)
    assert project_native_code_styles(source, actual, plan) == source
    assert (
        "".join(block.text for block in original.blocks) == "```tsconst elapsed: number = 150;```"
    )


def test_native_soft_breaks_tabs_and_non_latin_strings_remain_exact():
    text = "def label():\x0b\treturn '日本語 😀'\x0b\x0bprint(label())\n"
    source = _document([(text, "Courier New")])
    original = _protected(source)
    plan = plan_native_code_styles(source, original, original)
    assert plan.paragraphs[0].language == "python"
    actual = _apply(source, plan.requests)
    assert verify_native_code_styles(source, actual, plan)
    assert (
        "".join(
            code_string(code_object(part["textRun"])["content"])
            for part in _parts(_paragraph(actual, 0))
        )
        == text
    )
    assert project_native_code_styles(source, actual, plan) == source


@pytest.mark.parametrize(
    "change",
    [
        "link",
        "insert",
        "delete",
        "style_suggestion",
        "paragraph_suggestion",
        "unknown_part",
        "unknown_run",
        "unknown_style",
        "unknown_nested",
        "unknown_paragraph",
        "math",
        "symbol",
        "baseline",
        "index_gap",
        "boolean_index",
    ],
)
def test_unsafe_native_code_group_is_locked_but_never_colored(change: str):
    source = _document(
        [
            ("```python\n", "Arial"),
            ("def label():\n", "Courier New"),
            ("\treturn 1\n", "Courier New"),
            ("```\n", "Arial"),
        ]
    )
    original = _protected(source)
    paragraph = code_object(code_object(_body(source)[1])["paragraph"])
    part = code_object(code_array(paragraph["elements"])[0])
    run = code_object(part["textRun"])
    match change:
        case "link":
            code_object(run["textStyle"])["link"] = {"url": "https://example.test/code"}
        case "insert":
            part["suggestedInsertionIds"] = ["suggestion"]
        case "delete":
            part["suggestedDeletionIds"] = ["suggestion"]
        case "style_suggestion":
            run["suggestedTextStyleChanges"] = {"suggestion": {}}
        case "paragraph_suggestion":
            paragraph["suggestedParagraphStyleChanges"] = {"suggestion": {}}
        case "unknown_part":
            part["futureElement"] = True
        case "unknown_run":
            run["futureRun"] = True
        case "unknown_style":
            code_object(run["textStyle"])["futureStyle"] = True
        case "unknown_nested":
            code_object(code_object(run["textStyle"])["fontSize"])["futureDimension"] = True
        case "unknown_paragraph":
            code_object(paragraph["paragraphStyle"])["futureLayout"] = True
        case "math":
            code_object(code_object(run["textStyle"])["weightedFontFamily"])["fontFamily"] = (
                "Cambria Math"
            )
        case "symbol":
            code_object(code_object(run["textStyle"])["weightedFontFamily"])["fontFamily"] = (
                "Symbol"
            )
        case "baseline":
            code_object(run["textStyle"])["baselineOffset"] = "SUPERSCRIPT"
        case "index_gap":
            part["startIndex"] = code_index(part["startIndex"]) + 1
        case "boolean_index":
            part["startIndex"] = True
        case _:
            pytest.fail(f"Unknown unsafe-source mutation: {change}")
    protected = protect_native_code(original, source)
    assert all(not block.editable for block in protected.blocks)
    assert plan_native_code_styles(source, protected, protected).requests == []


@pytest.mark.parametrize(
    "change",
    [
        "text",
        "tab",
        "newline",
        "family",
        "size",
        "weight",
        "bold",
        "link",
        "suggestion",
        "unknown_style",
        "unknown_paragraph",
        "indent",
        "keep_with_next",
        "color",
        "shading",
        "parent",
        "range",
    ],
)
def test_readback_rejects_every_unplanned_code_or_style_change(change: str):
    source = _document([("def ready():\n", "Courier New"), ("\treturn 'yes'\n", "Courier New")])
    original = _protected(source)
    plan = plan_native_code_styles(source, original, original)
    actual = _apply(source, plan.requests)
    paragraph = code_object(code_object(_body(actual)[0])["paragraph"])
    part = code_object(code_array(paragraph["elements"])[0])
    style = code_object(code_object(part["textRun"])["textStyle"])
    match change:
        case "text":
            code_object(part["textRun"])["content"] = "x"
        case "tab":
            _run(actual, 1)["content"] = " "
        case "newline":
            code_object(code_object(code_array(paragraph["elements"])[-1])["textRun"])[
                "content"
            ] = " "
        case "family":
            code_object(style["weightedFontFamily"])["fontFamily"] = "Nunito"
        case "size":
            code_object(style["fontSize"])["magnitude"] = 18
        case "weight":
            code_object(style["weightedFontFamily"])["weight"] = 700
        case "bold":
            style["bold"] = True
        case "link":
            style["link"] = {"url": "https://example.test/changed"}
        case "suggestion":
            part["suggestedInsertionIds"] = ["new"]
        case "unknown_style":
            style["futureStyle"] = True
        case "unknown_paragraph":
            paragraph["futureParagraph"] = True
        case "indent":
            code_object(code_object(paragraph["paragraphStyle"])["indentStart"])["magnitude"] = 40
        case "keep_with_next":
            code_object(paragraph["paragraphStyle"])["keepWithNext"] = True
        case "color":
            style["foregroundColor"] = {"color": {"rgbColor": {"red": 1}}}
        case "shading":
            code_object(paragraph["paragraphStyle"])["shading"] = {}
        case "parent":
            actual["namedStyles"] = {"styles": []}
        case "range":
            code_object(_body(actual)[0])["startIndex"] = (
                code_index(code_object(_body(actual)[0])["startIndex"]) + 1
            )
        case _:
            pytest.fail(f"Unknown readback mutation: {change}")
    assert not verify_native_code_styles(source, actual, plan)


def test_projection_is_exactly_scoped_and_attestation_rejects_source_drift():
    source = _document(
        [("Opening prose.\n", "Arial"), ("SELECT value FROM samples;\n", "Courier New")]
    )
    original = _protected(source)
    plan = plan_native_code_styles(source, original, original)
    actual = _apply(source, plan.requests)
    code_object(code_object(code_object(_body(actual)[0])["paragraph"])["paragraphStyle"])[
        "spaceAbove"
    ] = {"magnitude": 99, "unit": "PT"}
    assert _body(project_native_code_styles(source, actual, plan))[0] == _body(actual)[0]
    altered_source = deepcopy(source)
    code_object(_run(altered_source, 1)["textStyle"])["bold"] = True
    assert not verify_native_code_styles(altered_source, actual, plan)
    altered_plan = replace(plan, paragraphs=(replace(plan.paragraphs[0], expected_json="{}"),))
    assert not verify_native_code_styles(source, actual, altered_plan)
