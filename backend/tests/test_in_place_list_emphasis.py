from pydantic import JsonValue

from app.document_transform import google_gateway
from app.document_transform.brand import DocsRequest, build_copy_text_mutations
from app.document_transform.ir import parse_google_document


LABEL = "Employee Performance Evaluations"
TAIL = ": collect 5 recent completed reviews from HR, anonymised, and upload them."
REWORDED = f"{LABEL}: collect 5 recent completed, anonymised reviews from HR and upload them."
BOLD: dict[str, JsonValue] = {"bold": True}
PLAIN: dict[str, JsonValue] = {}

type Run = tuple[str, dict[str, JsonValue]]


def _labelled_bullet(runs: list[Run]) -> dict[str, JsonValue]:
    elements: list[JsonValue] = []
    index = 1
    for content, style in [*runs, ("\n", PLAIN)]:
        elements.append(
            {
                "startIndex": index,
                "endIndex": index + len(content),
                "textRun": {"content": content, "textStyle": style},
            }
        )
        index += len(content)
    paragraph: JsonValue = {
        "startIndex": 1,
        "endIndex": index,
        "paragraph": {
            "bullet": {"listId": "list", "nestingLevel": 0},
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
            "elements": elements,
        },
    }
    return {
        "documentId": "doc",
        "revisionId": "revision",
        "tabs": [
            {
                "tabProperties": {"tabId": "tab"},
                "documentTab": {
                    "body": {"content": [paragraph]},
                    "lists": {"list": {"listProperties": {}}},
                },
                "childTabs": [],
            }
        ],
    }


def _apply_with_docs_style_inheritance(runs: list[Run], requests: list[DocsRequest]) -> list[Run]:
    characters = [(character, style) for text, style in runs for character in text]
    for request in requests:
        if "deleteContentRange" in request:
            target = request["deleteContentRange"]["range"]
            first, last = target["startIndex"] - 1, target["endIndex"] - 1
            characters = characters[:first] + characters[last:]
        if "insertText" in request:
            index = request["insertText"]["location"]["index"] - 1
            inherited = characters[max(index - 1, 0)][1] if characters else PLAIN
            inserted = [(character, inherited) for character in request["insertText"]["text"]]
            characters = characters[:index] + inserted + characters[index:]
    merged: list[Run] = []
    for character, style in characters:
        if merged and merged[-1][1] == style:
            merged[-1] = (merged[-1][0] + character, style)
        else:
            merged.append((character, style))
    return merged


def _protected_sha256(runs: list[Run]) -> str:
    inventory = google_gateway._protected_structure_inventory(
        _labelled_bullet(runs), include_native_list_text_styles=True
    )
    return google_gateway._inventory_sha256(inventory)


def test_rewording_a_labelled_bullet_keeps_its_bold_label_and_verifies() -> None:
    source: list[Run] = [(LABEL, BOLD), (TAIL, PLAIN)]
    original = parse_google_document(_labelled_bullet(source), title="Tasks")
    block = original.blocks[0]
    assert block.editable

    requests = build_copy_text_mutations(original, original.replace_blocks({block.id: REWORDED}))
    rewritten = _apply_with_docs_style_inheritance(source, requests)

    assert rewritten == [(LABEL, BOLD), (REWORDED.removeprefix(LABEL), PLAIN)]
    assert _protected_sha256(rewritten) == _protected_sha256(source)


def test_flattened_or_moved_emphasis_still_fails_verification() -> None:
    source: list[Run] = [(LABEL, BOLD), (TAIL, PLAIN)]

    assert _protected_sha256([(REWORDED, BOLD)]) != _protected_sha256(source)
    assert _protected_sha256([(LABEL, PLAIN), (TAIL, BOLD)]) != _protected_sha256(source)
